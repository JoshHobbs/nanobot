"""Omi wearable channel — polls for new conversations and feeds them to the agent."""

import asyncio
from typing import Any

import httpx
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import OmiConfig


class OmiClient:
    """Lightweight async HTTP client for the Omi REST API."""

    def __init__(self, api_key: str, api_url: str = "https://api.omi.me/v1/dev"):
        self.api_url = api_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def get_conversations(
        self, limit: int = 10, offset: int = 0, include_transcript: bool = False,
    ) -> list[dict]:
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if include_transcript:
            params["include_transcript"] = "true"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_url}/user/conversations",
                headers=self._headers,
                params=params,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_conversation(self, conv_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_url}/user/conversations/{conv_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()

    async def get_memories(self, limit: int = 10, offset: int = 0) -> list[dict]:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                f"{self.api_url}/user/memories",
                headers=self._headers,
                params={"limit": limit, "offset": offset},
            )
            resp.raise_for_status()
            return resp.json()

    async def create_memory(self, content: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self.api_url}/user/memories",
                headers=self._headers,
                json={"content": content},
            )
            resp.raise_for_status()
            return resp.json()

    async def edit_memory(self, memory_id: str, content: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(
                f"{self.api_url}/user/memories/{memory_id}",
                headers=self._headers,
                json={"content": content},
            )
            resp.raise_for_status()
            return resp.json()

    async def delete_memory(self, memory_id: str) -> dict:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.delete(
                f"{self.api_url}/user/memories/{memory_id}",
                headers=self._headers,
            )
            resp.raise_for_status()
            return resp.json()


class OmiChannel(BaseChannel):
    """
    Omi wearable channel.

    Polls the Omi API for new conversations captured by the wearable
    and forwards them to the agent as inbound messages. Outbound
    responses are stored as Omi memories.
    """

    name: str = "omi"

    def __init__(self, config: OmiConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: OmiConfig = config
        self.client = OmiClient(config.api_key, config.api_url)
        self._seen_ids: set[str] = set()

    async def start(self) -> None:
        if self._running:
            return

        if not self.config.api_key:
            logger.error("Omi API key not configured")
            return

        self._running = True
        logger.info("Starting Omi wearable channel...")

        # Seed seen IDs so we don't replay old conversations on startup
        try:
            existing = await self.client.get_conversations(limit=25)
            for conv in existing:
                cid = conv.get("id")
                if cid:
                    self._seen_ids.add(cid)
            logger.info(f"Omi: seeded {len(self._seen_ids)} existing conversations")
        except Exception as e:
            logger.warning(f"Omi: failed to seed conversations: {e}")

        await self._poll_loop()

    async def stop(self) -> None:
        self._running = False

    async def send(self, msg: OutboundMessage) -> None:
        """Store the agent's response as an Omi memory."""
        if not msg.content:
            return
        try:
            await self.client.create_memory(msg.content)
            logger.debug(f"Omi: created memory from response ({len(msg.content)} chars)")
        except Exception as e:
            logger.error(f"Omi: failed to create memory: {e}")

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                convos = await self.client.get_conversations(
                    limit=10, include_transcript=True,
                )
                new_count = sum(1 for c in convos if c.get("id") and c["id"] not in self._seen_ids)
                logger.debug(f"Omi poll: {len(convos)} conversations, {new_count} new")
                for conv in convos:
                    cid = conv.get("id")
                    if not cid or cid in self._seen_ids:
                        continue
                    self._seen_ids.add(cid)

                    # Build message text from conversation
                    text = self._format_conversation(conv)
                    if not text:
                        continue

                    sender = "omi-wearable"
                    logger.info(f"Omi: new conversation {cid}: {text[:60]}...")

                    await self._handle_message(
                        sender_id=sender,
                        chat_id=cid,
                        content=text,
                        metadata={
                            "conversation_id": cid,
                            "title": conv.get("title", ""),
                            "category": conv.get("category", ""),
                        },
                    )
            except Exception as e:
                logger.error(f"Omi poll error: {e}")

            await asyncio.sleep(self.config.poll_interval)

    @staticmethod
    def _format_conversation(conv: dict) -> str:
        """Format an Omi conversation into readable text for the agent."""
        parts = []

        title = conv.get("title")
        if title:
            parts.append(f"Conversation: {title}")

        overview = conv.get("overview")
        if overview:
            parts.append(overview)

        # Include transcript segments if available
        transcript = conv.get("transcript")
        if transcript and isinstance(transcript, list):
            lines = []
            for seg in transcript:
                speaker = seg.get("speaker", "?")
                text = seg.get("text", "")
                if text:
                    lines.append(f"[Speaker {speaker}]: {text}")
            if lines:
                parts.append("\n".join(lines))

        return "\n\n".join(parts)
