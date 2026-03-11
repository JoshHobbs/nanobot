# Codebase Review

Date: 2026-02-13

## Findings (high to low)

### 1) High — Arbitrary command execution through `exec` tool remains effectively unrestricted
- File: `nanobot/agent/tools/shell.py:76-82`, `nanobot/agent/tools/shell.py:119-162`
- `create_subprocess_shell` executes raw shell strings with best-effort regex guards only.
- `nanobot/agent/loop.py:117-121` registers `exec` by default, so it is available without explicit opt-in.

### 2) High — Sub-agent coding tool runs in bypass mode
- File: `nanobot/agent/tools/claude_code.py:59-65`, `nanobot/agent/loop.py:139-141`
- The tool always invokes `claude --dangerously-skip-permissions` and is always registered.
- This bypasses important runtime safety controls and can be abused when tool calling is enabled.

### 3) Medium — SSRF risk in outbound web fetch
- File: `nanobot/agent/tools/web.py:33-41`, `nanobot/agent/tools/web.py:122-125`
- URL validation only checks scheme/domain, then follows redirects (`max_redirects=5`).
- No host allowlist or private/LAN/localhost IP filtering is enforced.

### 4) Medium — Cron store writes are non-atomic
- File: `nanobot/cron/service.py:118-162`
- `_save_store()` writes directly to `self.store_path` in-place.
- Power loss / interruption can leave `jobs.json` truncated/corrupt.

### 5) Medium — Workspace restriction default is disabled
- File: `nanobot/config/schema.py:294`, `nanobot/agent/loop.py:109-121`
- `restrict_to_workspace` defaults to `False`, so file and shell operations are not confinement-first by default.

### 6) Medium — Config writes are non-atomic
- File: `nanobot/config/loader.py:56-63`
- `save_config()` writes directly to config path with `open(..., "w")`, allowing partial truncation on interruption.

## Assumptions / Open Questions
1. Are external/untrusted users allowed to invoke tools? If yes, risks in findings #1, #2, and #5 are significantly higher.
2. Is there an upstream network policy for outbound HTTP that blocks private ranges and internal metadata endpoints? If not, finding #3 is higher.

## Suggested Fix Summary
1. Tighten `exec` tool: avoid shell parser where possible, use explicit arg lists, strict allowlist patterns, and default tool disable.
2. Guard `claude_code`: require explicit opt-in config and remove `--dangerously-skip-permissions` unless explicitly enabled.
3. Add SSRF guardrails in `web_fetch`: deny localhost/private IPs, block metadata IP ranges, and constrain redirects to allowed hosts.
4. Make state/config writes atomic with temp file + `replace()` (and fsync where practical).
