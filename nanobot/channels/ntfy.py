"""ntfy channel — subscribe to a topic for inbound messages, publish responses."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import NtfyConfig


class NtfyChannel(BaseChannel):
    """
    ntfy pub/sub channel.

    Subscribes to a topic via SSE for incoming messages and publishes
    agent responses back to the same (or a different) topic.
    """

    name: str = "ntfy"

    def __init__(self, config: NtfyConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: NtfyConfig = config
        self._base_url = config.server_url.rstrip("/")
        self._subscribe_url = f"{self._base_url}/{config.topic}/sse"
        self._publish_url = f"{self._base_url}/{config.topic}"

    async def start(self) -> None:
        if self._running:
            return

        if not self.config.topic:
            logger.error("ntfy topic not configured")
            return

        self._running = True
        logger.info(f"Starting ntfy channel (topic: {self.config.topic})...")

        while self._running:
            try:
                await self._sse_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"ntfy SSE connection error: {e}")
                if self._running:
                    await asyncio.sleep(5)

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Publish a message to the ntfy topic."""
        if not msg.content:
            return

        headers = self._auth_headers()
        headers["Content-Type"] = "text/plain"
        if self.config.markdown:
            headers["Markdown"] = "yes"

        priority = msg.metadata.get("priority") if msg.metadata else None
        if priority:
            headers["Priority"] = str(priority)

        title = msg.metadata.get("title") if msg.metadata else None
        if title:
            headers["Title"] = title

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    self._publish_url,
                    headers=headers,
                    content=msg.content.encode("utf-8"),
                )
                resp.raise_for_status()
                logger.debug(f"ntfy: published message ({len(msg.content)} chars)")
        except Exception as e:
            logger.error(f"ntfy: failed to publish message: {e}")

    async def _sse_loop(self) -> None:
        """Subscribe to the ntfy topic via SSE and process incoming messages."""
        headers = self._auth_headers()

        async with httpx.AsyncClient(timeout=None) as client:
            async with client.stream(
                "GET", self._subscribe_url, headers=headers
            ) as resp:
                resp.raise_for_status()
                logger.info(f"ntfy: connected to {self._subscribe_url}")

                async for line in resp.aiter_lines():
                    if not self._running:
                        break
                    if not line or not line.startswith("data: "):
                        continue

                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    if data.get("event") != "message":
                        continue

                    text = data.get("message", "").strip()
                    if not text:
                        continue

                    sender = data.get("title", "ntfy")
                    topic = data.get("topic", self.config.topic)

                    logger.debug(f"ntfy: received message from {sender}: {text[:60]}...")

                    await self._handle_message(
                        sender_id=sender,
                        chat_id=topic,
                        content=text,
                        metadata={
                            "ntfy_id": data.get("id", ""),
                            "priority": data.get("priority", 3),
                            "tags": data.get("tags", []),
                        },
                    )

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers
