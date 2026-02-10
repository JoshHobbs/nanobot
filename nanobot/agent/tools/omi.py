"""Omi wearable tools — query conversations and manage memories on demand."""

import json
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.channels.omi import OmiClient


class OmiConversationsTool(Tool):
    """Get recent conversations captured by the Omi wearable."""

    def __init__(self, api_key: str, api_url: str = "https://api.omi.me/v1/dev"):
        self._client = OmiClient(api_key, api_url)

    @property
    def name(self) -> str:
        return "omi_conversations"

    @property
    def description(self) -> str:
        return (
            "Get recent conversations captured by the Omi wearable device. "
            "Returns transcripts, titles, and overviews of real-world conversations."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of conversations to retrieve (default 5, max 25)",
                    "minimum": 1,
                    "maximum": 25,
                },
                "conversation_id": {
                    "type": "string",
                    "description": "Get a specific conversation by ID (overrides limit)",
                },
            },
            "required": [],
        }

    async def execute(self, **kwargs: Any) -> str:
        conv_id = kwargs.get("conversation_id")
        if conv_id:
            try:
                conv = await self._client.get_conversation(conv_id)
                return json.dumps(conv, indent=2, default=str)
            except Exception as e:
                return f"Error fetching conversation {conv_id}: {e}"

        limit = min(kwargs.get("limit", 5), 25)
        try:
            convos = await self._client.get_conversations(
                limit=limit, include_transcript=True,
            )
            if not convos:
                return "No conversations found."
            return json.dumps(convos, indent=2, default=str)
        except Exception as e:
            return f"Error fetching conversations: {e}"


class OmiMemoriesTool(Tool):
    """Manage memories stored in the Omi wearable platform."""

    def __init__(self, api_key: str, api_url: str = "https://api.omi.me/v1/dev"):
        self._client = OmiClient(api_key, api_url)

    @property
    def name(self) -> str:
        return "omi_memories"

    @property
    def description(self) -> str:
        return (
            "Manage memories in the Omi wearable platform. "
            "Actions: list (get recent memories), create (store a new memory), "
            "edit (update an existing memory), delete (remove a memory)."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "create", "edit", "delete"],
                    "description": "The action to perform",
                },
                "content": {
                    "type": "string",
                    "description": "Memory content (required for create/edit)",
                },
                "memory_id": {
                    "type": "string",
                    "description": "Memory ID (required for edit/delete)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Number of memories to list (default 10)",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["action"],
        }

    async def execute(self, **kwargs: Any) -> str:
        action = kwargs.get("action", "list")

        if action == "list":
            limit = min(kwargs.get("limit", 10), 50)
            try:
                memories = await self._client.get_memories(limit=limit)
                if not memories:
                    return "No memories found."
                return json.dumps(memories, indent=2, default=str)
            except Exception as e:
                return f"Error listing memories: {e}"

        elif action == "create":
            content = kwargs.get("content")
            if not content:
                return "Error: content is required for create action"
            try:
                result = await self._client.create_memory(content)
                return f"Memory created: {json.dumps(result, default=str)}"
            except Exception as e:
                return f"Error creating memory: {e}"

        elif action == "edit":
            memory_id = kwargs.get("memory_id")
            content = kwargs.get("content")
            if not memory_id or not content:
                return "Error: memory_id and content are required for edit action"
            try:
                result = await self._client.edit_memory(memory_id, content)
                return f"Memory updated: {json.dumps(result, default=str)}"
            except Exception as e:
                return f"Error editing memory: {e}"

        elif action == "delete":
            memory_id = kwargs.get("memory_id")
            if not memory_id:
                return "Error: memory_id is required for delete action"
            try:
                await self._client.delete_memory(memory_id)
                return f"Memory {memory_id} deleted."
            except Exception as e:
                return f"Error deleting memory: {e}"

        else:
            return f"Error: unknown action '{action}'. Use list, create, edit, or delete."
