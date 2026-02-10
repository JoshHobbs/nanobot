"""Claude Code tool – runs Claude Code CLI in autonomous mode."""

import asyncio
import os
from typing import Any

from nanobot.agent.tools.base import Tool


class ClaudeCodeTool(Tool):
    """Tool to run Claude Code (claude CLI) in fully autonomous mode."""

    def __init__(self, working_dir: str | None = None, timeout: int = 300):
        self.working_dir = working_dir
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "claude_code"

    @property
    def description(self) -> str:
        return (
            "Run Claude Code (Anthropic's AI coding agent) in autonomous mode. "
            "Give it a task and it will read, write, and edit files, run commands, "
            "and complete complex software engineering tasks. Use this for tasks "
            "that require deep code understanding, multi-file edits, debugging, "
            "or any substantial coding work."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "The task or instruction for Claude Code to execute",
                },
                "working_dir": {
                    "type": "string",
                    "description": "Optional working directory (defaults to workspace)",
                },
            },
            "required": ["prompt"],
        }

    async def execute(self, prompt: str, working_dir: str | None = None, **kwargs: Any) -> str:
        cwd = working_dir or self.working_dir or os.getcwd()

        cmd = [
            "claude",
            "--print",
            "--dangerously-skip-permissions",
            "--max-turns", "50",
            prompt,
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=self.timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                return f"Error: Claude Code timed out after {self.timeout} seconds"

            output_parts = []

            if stdout:
                output_parts.append(stdout.decode("utf-8", errors="replace"))

            if stderr:
                stderr_text = stderr.decode("utf-8", errors="replace")
                if stderr_text.strip():
                    output_parts.append(f"STDERR:\n{stderr_text}")

            if process.returncode != 0:
                output_parts.append(f"\nExit code: {process.returncode}")

            result = "\n".join(output_parts) if output_parts else "(no output)"

            max_len = 15000
            if len(result) > max_len:
                result = result[:max_len] + f"\n... (truncated, {len(result) - max_len} more chars)"

            return result

        except FileNotFoundError:
            return "Error: 'claude' CLI not found. Is Claude Code installed?"
        except Exception as e:
            return f"Error running Claude Code: {str(e)}"
