from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.discord import DiscordChannel
from nanobot.config.schema import DiscordConfig
from nanobot.cron.service import _compute_next_run
from nanobot.cron.types import CronSchedule
from nanobot.providers.base import LLMProvider, LLMResponse


class SampleTool(Tool):
    @property
    def name(self) -> str:
        return "sample"

    @property
    def description(self) -> str:
        return "sample tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {"type": "string", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
                "mode": {"type": "string", "enum": ["fast", "full"]},
                "meta": {
                    "type": "object",
                    "properties": {
                        "tag": {"type": "string"},
                        "flags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["tag"],
                },
            },
            "required": ["query", "count"],
        }

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


def test_validate_params_missing_required() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi"})
    assert "missing required count" in "; ".join(errors)


def test_validate_params_type_and_range() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 0})
    assert any("count must be >= 1" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": "2"})
    assert any("count should be integer" in e for e in errors)

    errors = tool.validate_params({"query": "hi", "count": True})
    assert any("count should be integer" in e for e in errors)


def test_validate_params_enum_and_min_length() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "h", "count": 2, "mode": "slow"})
    assert any("query must be at least 2 chars" in e for e in errors)
    assert any("mode must be one of" in e for e in errors)


def test_validate_params_nested_object_and_array() -> None:
    tool = SampleTool()
    errors = tool.validate_params(
        {
            "query": "hi",
            "count": 2,
            "meta": {"flags": [1, "ok"]},
        }
    )
    assert any("missing required meta.tag" in e for e in errors)
    assert any("meta.flags[0] should be string" in e for e in errors)


def test_validate_params_ignores_unknown_fields() -> None:
    tool = SampleTool()
    errors = tool.validate_params({"query": "hi", "count": 2, "extra": "x"})
    assert errors == []


async def test_registry_returns_validation_error() -> None:
    reg = ToolRegistry()
    reg.register(SampleTool())
    result = await reg.execute("sample", {"query": "hi"})
    assert "Invalid parameters" in result


class _DummyProvider(LLMProvider):
    async def chat(
        self,
        messages,
        tools=None,
        model=None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        return LLMResponse(content="ok")

    def get_default_model(self) -> str:
        return "dummy"


class _FakeSession:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def get_history(self, max_messages: int = 50) -> list[dict[str, str]]:
        return []

    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        self.messages.append((role, content))


class _FakeSessionManager:
    def __init__(self) -> None:
        self.keys: list[str] = []

    def get_or_create(self, key: str) -> _FakeSession:
        self.keys.append(key)
        return _FakeSession()

    def save(self, session: _FakeSession) -> None:
        return None


async def test_process_direct_uses_explicit_session_key() -> None:
    sessions = _FakeSessionManager()
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_DummyProvider(),
        workspace=Path("."),
        session_manager=sessions,
    )

    await loop.process_direct("hello", session_key="cli:first")
    await loop.process_direct("world", session_key="cli:second")

    assert sessions.keys == ["cli:first", "cli:second"]


class _FakeResponse:
    status_code = 200
    text = ""

    def raise_for_status(self) -> None:
        return None


class _FakeHttpClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def post(self, url: str, headers=None, json=None):  # noqa: A002
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse()


async def test_discord_send_uses_metadata_reply_to_when_reply_to_field_empty() -> None:
    channel = DiscordChannel(
        config=DiscordConfig(enabled=True, token="test-token"),
        bus=MessageBus(),
    )
    channel._http = _FakeHttpClient()

    await channel.send(
        OutboundMessage(
            channel="discord",
            chat_id="123",
            content="reply",
            metadata={"reply_to": "987654321"},
        )
    )

    call = channel._http.calls[0]
    assert call["json"]["message_reference"]["message_id"] == "987654321"
    assert call["json"]["allowed_mentions"] == {"replied_user": False}


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_compute_next_run_cron_honors_timezone() -> None:
    # 2026-02-11 13:30 UTC is 08:30 in America/New_York (EST).
    now_ms = _ms(datetime(2026, 2, 11, 13, 30, tzinfo=timezone.utc))
    schedule = CronSchedule(kind="cron", expr="0 9 * * *", tz="America/New_York")

    next_run_ms = _compute_next_run(schedule, now_ms)

    expected_ms = _ms(datetime(2026, 2, 11, 14, 0, tzinfo=timezone.utc))
    assert next_run_ms == expected_ms
