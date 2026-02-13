"""Token usage tracking — appends one JSON line per LLM call."""

import json
import time
from pathlib import Path

from loguru import logger

# Default location: ~/.nanobot/data/usage.jsonl
DEFAULT_USAGE_PATH = Path.home() / ".nanobot" / "data" / "usage.jsonl"


class UsageTracker:
    """Appends token usage records to a JSONL file."""

    def __init__(self, path: Path = DEFAULT_USAGE_PATH):
        self.path = path

    def record(self, model: str, usage: dict[str, int]) -> None:
        """Record a single LLM call's token usage.

        No-ops silently if usage is empty (error responses, providers that
        don't report tokens).
        """
        if not usage:
            return

        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "model": model,
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        }

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"Failed to write usage record: {e}")
