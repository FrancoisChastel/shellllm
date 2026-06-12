"""Opt-in terminal context: what just happened in this pane.

The zsh wrappers capture the previous command, its exit status, recent
history, and (inside tmux) recent pane output, and pass them down via
environment variables. This module turns those into a small, redacted
system block — so `, why did that fail` and `? what does that error
mean` work without re-typing anything.

Privacy ladder — everything is off unless the user sets
``SHELLLM_SHELL_CONTEXT``:

    off       (default) nothing is captured or injected
    cmd       previous command + exit status
    history   + last few commands
    output    + recent pane output (tmux only)

Both the zsh side (capture) and this module (injection) enforce the
ladder independently, so a stale exported variable can never leak past
the configured level. Unknown levels fail safe to ``off``. All values
pass through :func:`redact` before injection; with a local model the
data never leaves the machine anyway, but ``SHELLLM_BASE_URL`` may
point at a hosted API.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

LEVEL_ENV = "SHELLLM_SHELL_CONTEXT"
LEVELS = ("off", "cmd", "history", "output")

MAX_LAST_CMD_CHARS = 500
MAX_HISTORY_LINES = 10
MAX_HISTORY_LINE_CHARS = 200
MAX_OUTPUT_CHARS = 4_000
MAX_PIPED_CHARS = 16_000

_REDACTED = "[redacted]"

# Keyed assignments/headers: KEY=value, key: value. The value is dropped,
# the key kept so the model still sees *what* was being set.
_KEYED = re.compile(
    r"(?i)\b([A-Za-z0-9_-]*(?:api[_-]?key|access[_-]?key|secret|token|passw(?:or)?d|credential)"
    r"[A-Za-z0-9_-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)

# Well-known token shapes. Deliberately NOT a generic long-blob pattern:
# 40-hex git SHAs are useful context and must survive redaction.
_TOKEN_SHAPES = [
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]+"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bsk[-_](?:live[-_]|test[-_])?[A-Za-z0-9_-]{10,}\b"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
]


def redact(text: str) -> str:
    """Strip likely secrets from a capture before it reaches the model."""
    out = _KEYED.sub(lambda m: f"{m.group(1)}{m.group(2)}{_REDACTED}", text)
    for pattern in _TOKEN_SHAPES:
        out = pattern.sub(_REDACTED, out)
    return out


def _level(env: Mapping[str, str]) -> int:
    """Return the ladder index for the configured level; 0 == off."""
    raw = env.get(LEVEL_ENV, "off").strip().lower()
    try:
        return LEVELS.index(raw)
    except ValueError:
        return 0


def build_shell_context_block(env: Mapping[str, str] | None = None) -> str:
    """Render the terminal-context system block, or "" when off / empty.

    ``env`` is injectable for tests; defaults to ``os.environ``.
    """
    env = os.environ if env is None else env
    level = _level(env)
    if level == 0:
        return ""

    parts: list[str] = []

    last_cmd = env.get("SHELLLM_LAST_CMD", "").strip()
    if last_cmd:
        parts.append(f"- previous command: `{redact(last_cmd[:MAX_LAST_CMD_CHARS])}`")

    raw_status = env.get("SHELLLM_LAST_STATUS", "").strip()
    if raw_status:
        try:
            status = int(raw_status)
        except ValueError:
            status = None
        if status is not None:
            suffix = "" if status == 0 else " (it failed)"
            parts.append(f"- its exit status: {status}{suffix}")

    if level >= LEVELS.index("history"):
        history = env.get("SHELLLM_RECENT_HISTORY", "").strip()
        if history:
            lines = [ln.strip()[:MAX_HISTORY_LINE_CHARS] for ln in history.splitlines() if ln.strip()]
            lines = lines[-MAX_HISTORY_LINES:]
            if lines:
                joined = "\n".join(f"    {redact(ln)}" for ln in lines)
                parts.append(f"- recent commands (oldest first):\n{joined}")

    if level >= LEVELS.index("output"):
        output = env.get("SHELLLM_PANE_OUTPUT", "").strip()
        if output:
            tail = output[-MAX_OUTPUT_CHARS:]
            parts.append(f"- recent terminal output (most recent last):\n{redact(tail)}")

    if not parts:
        return ""

    return "\n".join(
        [
            "Terminal context (the user opted in to sharing this; secrets are redacted):",
            *parts,
            "Use this to resolve references like 'that command', 'the error', or 'why did it fail'.",
        ]
    )


def build_piped_block(text: str) -> str:
    """Render piped stdin as a system block, or "" when empty.

    Piping is explicit consent — no ladder gate — but the content still
    goes through :func:`redact`. Errors usually sit at the end of a
    capture, so oversized input keeps the tail.
    """
    text = text.strip()
    if not text:
        return ""
    truncated = len(text) > MAX_PIPED_CHARS
    if truncated:
        text = text[-MAX_PIPED_CHARS:]
    header = "Piped input (the user piped this into the command"
    header += f"; truncated to the last {MAX_PIPED_CHARS} characters):" if truncated else "):"
    return f"{header}\n{redact(text)}"
