"""Signal channel implementation using signal-cli JSON-RPC mode."""

import asyncio
import json
import shutil
from collections import OrderedDict
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import SignalConfig

MAX_SIGNAL_LENGTH = 2000


class SignalChannel(BaseChannel):
    """
    Signal channel using signal-cli's JSON-RPC subprocess.

    Starts signal-cli in jsonRpc mode as a persistent subprocess.
    Reads incoming messages from stdout, sends via stdin.
    """

    name: str = "signal"

    def __init__(self, config: SignalConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: SignalConfig = config
        self._process: asyncio.subprocess.Process | None = None
        self._request_id: int = 0
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._write_lock: asyncio.Lock = asyncio.Lock()
        self._seen_messages: OrderedDict[tuple[str, int], None] = OrderedDict()

    async def start(self) -> None:
        """Start the signal-cli JSON-RPC subprocess."""
        if self._running:
            return

        if not self.config.account:
            logger.error("Signal account number not configured (e.g. +14155551234)")
            return

        cli_path = self.config.cli_path or "signal-cli"
        if not shutil.which(cli_path):
            logger.error(
                f"signal-cli not found at '{cli_path}'. "
                "Install it: https://github.com/AsamK/signal-cli"
            )
            return

        self._running = True

        cmd = [cli_path, "-a", self.config.account, "jsonRpc"]
        if self.config.config_path:
            cmd.insert(1, "--config")
            cmd.insert(2, self.config.config_path)

        logger.info(f"Starting signal-cli JSON-RPC for {self.config.account}...")

        while self._running:
            try:
                self._process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                logger.info("signal-cli JSON-RPC subprocess started")

                stderr_task = asyncio.create_task(self._stderr_logger())
                await self._reader_loop()
                stderr_task.cancel()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"signal-cli subprocess error: {e}")

            self._fail_pending_requests("signal-cli process exited")

            if self._running:
                logger.info("Restarting signal-cli in 5 seconds...")
                await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the signal-cli subprocess."""
        self._running = False
        self._fail_pending_requests("channel stopping")

        if self._process:
            try:
                self._process.terminate()
                try:
                    await asyncio.wait_for(self._process.wait(), timeout=5.0)
                except asyncio.TimeoutError:
                    self._process.kill()
            except ProcessLookupError:
                pass
            self._process = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message via signal-cli JSON-RPC."""
        if not msg.content:
            return

        if len(msg.content) <= MAX_SIGNAL_LENGTH:
            await self._send_single(msg.chat_id, msg.content, msg.metadata)
        else:
            chunks = self._split_message(msg.content, MAX_SIGNAL_LENGTH)
            for chunk in chunks:
                await self._send_single(msg.chat_id, chunk, msg.metadata)
                await asyncio.sleep(0.5)

    async def _send_single(self, chat_id: str, text: str, metadata: dict) -> None:
        """Send a single message via JSON-RPC."""
        if not self._process or not self._process.stdin:
            logger.warning("signal-cli not running, cannot send")
            return

        self._request_id += 1
        req_id = self._request_id

        params: dict[str, Any] = {"message": text}
        if metadata.get("is_group"):
            params["groupId"] = chat_id
        else:
            params["recipient"] = [chat_id]

        request = {
            "jsonrpc": "2.0",
            "method": "send",
            "params": params,
            "id": req_id,
        }

        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_requests[req_id] = future

        try:
            line = json.dumps(request) + "\n"
            async with self._write_lock:
                self._process.stdin.write(line.encode())
                await self._process.stdin.drain()

            result = await asyncio.wait_for(future, timeout=30.0)
            logger.debug(f"Signal message sent to {chat_id}")
        except asyncio.TimeoutError:
            self._pending_requests.pop(req_id, None)
            logger.error(f"Timeout sending Signal message to {chat_id}")
        except Exception as e:
            self._pending_requests.pop(req_id, None)
            logger.error(f"Error sending Signal message: {e}")

    async def _reader_loop(self) -> None:
        """Read lines from signal-cli stdout and dispatch."""
        if not self._process or not self._process.stdout:
            return

        while self._running:
            line = await self._process.stdout.readline()
            if not line:
                logger.warning("signal-cli stdout closed")
                break

            try:
                data = json.loads(line.decode().strip())
            except json.JSONDecodeError:
                logger.debug(f"Non-JSON from signal-cli: {line.decode().strip()[:200]}")
                continue

            # JSON-RPC response to a request we sent
            if "id" in data and data["id"] in self._pending_requests:
                future = self._pending_requests.pop(data["id"])
                if "error" in data:
                    future.set_exception(
                        RuntimeError(f"signal-cli error: {data['error']}")
                    )
                else:
                    future.set_result(data.get("result"))
                continue

            # JSON-RPC notification (incoming message)
            if "method" in data:
                asyncio.create_task(self._handle_jsonrpc_event(data))
                continue

            # Raw envelope (some signal-cli versions)
            if "envelope" in data:
                asyncio.create_task(self._handle_envelope(data["envelope"]))
                continue

            logger.debug(f"Unhandled signal-cli output: {json.dumps(data)[:200]}")

    async def _handle_jsonrpc_event(self, data: dict) -> None:
        """Handle a JSON-RPC notification from signal-cli."""
        params = data.get("params", {})
        envelope = params.get("envelope", params)
        await self._handle_envelope(envelope)

    async def _handle_envelope(self, envelope: dict) -> None:
        """Parse a Signal envelope and forward to the message bus."""
        source = envelope.get("source") or envelope.get("sourceNumber", "")
        timestamp = envelope.get("timestamp")

        if not source:
            return

        # Dedup
        if timestamp:
            msg_key = (source, timestamp)
            if msg_key in self._seen_messages:
                return
            self._seen_messages[msg_key] = None
            while len(self._seen_messages) > 1000:
                self._seen_messages.popitem(last=False)

        data_msg = envelope.get("dataMessage")
        if not data_msg:
            return

        text = data_msg.get("message") or data_msg.get("body") or ""

        # Attachments
        media_paths = []
        for att in data_msg.get("attachments", []):
            file_path = att.get("filename") or att.get("id", "")
            if file_path:
                media_paths.append(file_path)
                if not text:
                    text = f"[{att.get('contentType', 'attachment')}]"

        # Group vs DM
        group_info = data_msg.get("groupInfo") or data_msg.get("group")
        group_id = group_info.get("groupId") if group_info else None
        chat_id = group_id if group_id else source

        if not text:
            return

        logger.info(f"Signal message from {source}: {text[:50]}...")

        await self._handle_message(
            sender_id=source,
            chat_id=chat_id,
            content=text,
            media=media_paths,
            metadata={
                "timestamp": timestamp,
                "is_group": bool(group_id),
                "group_id": group_id,
            },
        )

    def _fail_pending_requests(self, reason: str) -> None:
        """Fail all pending request futures."""
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(RuntimeError(reason))
        self._pending_requests.clear()

    async def _stderr_logger(self) -> None:
        """Log signal-cli stderr output."""
        if not self._process or not self._process.stderr:
            return
        while True:
            line = await self._process.stderr.readline()
            if not line:
                break
            text = line.decode().strip()
            if text:
                logger.debug(f"signal-cli: {text}")

    @staticmethod
    def _split_message(text: str, max_len: int) -> list[str]:
        """Split a long message into chunks at paragraph boundaries."""
        if len(text) <= max_len:
            return [text]

        chunks = []
        while text:
            if len(text) <= max_len:
                chunks.append(text)
                break

            # Try to split at paragraph boundary
            split_at = text.rfind("\n\n", 0, max_len)
            if split_at == -1:
                split_at = text.rfind("\n", 0, max_len)
            if split_at == -1:
                split_at = text.rfind(" ", 0, max_len)
            if split_at == -1:
                split_at = max_len

            chunks.append(text[:split_at].rstrip())
            text = text[split_at:].lstrip()

        return chunks
