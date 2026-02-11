"""ntfy channel — output-only notification channel."""

from __future__ import annotations

import asyncio

import httpx
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import NtfyConfig


class NtfyChannel(BaseChannel):
    """
    ntfy output-only channel.

    Publishes agent messages to an ntfy topic as push notifications.
    Does not subscribe for inbound — use the message tool to route
    notifications here from other channels.
    """

    name: str = "ntfy"

    def __init__(self, config: NtfyConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: NtfyConfig = config
        self._base_url = config.server_url.rstrip("/")
        self._publish_url = f"{self._base_url}/{config.topic}"

    async def start(self) -> None:
        if self._running:
            return

        if not self.config.topic:
            logger.error("ntfy topic not configured")
            return

        self._running = True
        logger.info(f"ntfy channel ready (topic: {self.config.topic})")

        # Output-only — just idle until stopped
        while self._running:
            await asyncio.sleep(60)

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

    def _auth_headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.config.auth_token:
            headers["Authorization"] = f"Bearer {self.config.auth_token}"
        return headers
