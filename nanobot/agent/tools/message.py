"""Message tool for sending messages to users."""

from typing import Any, Callable, Awaitable

from nanobot.agent.tools.base import Tool
from nanobot.bus.events import OutboundMessage


class MessageTool(Tool):
    """Tool to send messages to users on chat channels."""

    def __init__(
        self,
        send_callback: Callable[[OutboundMessage], Awaitable[None]] | None = None,
        default_channel: str = "",
        default_chat_id: str = ""
    ):
        self._send_callback = send_callback
        self._default_channel = default_channel
        self._default_chat_id = default_chat_id
        self._available_channels: list[str] = []

    def set_context(self, channel: str, chat_id: str) -> None:
        """Set the current message context."""
        self._default_channel = channel
        self._default_chat_id = chat_id

    def set_send_callback(self, callback: Callable[[OutboundMessage], Awaitable[None]]) -> None:
        """Set the callback for sending messages."""
        self._send_callback = callback

    def set_available_channels(self, channels: list[str]) -> None:
        """Set the list of enabled channels for tool description."""
        self._available_channels = channels

    @property
    def name(self) -> str:
        return "message"

    @property
    def description(self) -> str:
        return "Send a message to the user. Use this when you want to communicate something."

    @property
    def parameters(self) -> dict[str, Any]:
        if self._available_channels:
            channel_desc = f"Target channel. Available: {', '.join(self._available_channels)}"
        else:
            channel_desc = "Target channel (e.g. telegram, discord, ntfy)"
        return {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The message content to send"
                },
                "channel": {
                    "type": "string",
                    "description": channel_desc
                },
                "chat_id": {
                    "type": "string",
                    "description": "Target chat/user ID (for output-only channels like ntfy, use any value)"
                }
            },
            "required": ["content"]
        }
    
    async def execute(
        self, 
        content: str, 
        channel: str | None = None, 
        chat_id: str | None = None,
        **kwargs: Any
    ) -> str:
        channel = channel or self._default_channel
        chat_id = chat_id or self._default_chat_id
        
        if not channel or not chat_id:
            return "Error: No target channel/chat specified"
        
        if not self._send_callback:
            return "Error: Message sending not configured"
        
        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content
        )
        
        try:
            await self._send_callback(msg)
            return f"Message sent to {channel}:{chat_id}"
        except Exception as e:
            return f"Error sending message: {str(e)}"
