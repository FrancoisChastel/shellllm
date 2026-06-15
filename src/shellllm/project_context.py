"""Per-project context via ``.shellllmrc``.

Walks up from the current working directory looking for a
``.shellllmrc`` file, stopping at ``$HOME`` (we never look outside the
user's home). The innermost file wins — same precedence as
``.editorconfig`` or ``.gitignore``.

The file is plain text. Its contents become a "Project context" system
message that rides along with every ``,`` and ``?`` invocation in that
directory tree, letting users encode persistent project conventions
("this project uses pnpm", "prefer ripgrep over grep", "we target
Python 3.11+") without re-typing them every call.

The file is small, frequently read, and read fresh every call — no
caching. If you want a different style, edit the file and the next
call picks it up.
"""

from __future__ import annotations

import os
from pathlib import Path

RC_NAME = ".shellllmrc"
MAX_RC_BYTES = 4_000  # plenty for a paragraph of project rules


def _home() -> Path:
    """Return ``$HOME`` resolved, or the literal "/" if HOME is unset."""
    raw = os.environ.get("HOME", "")
    return Path(raw).expanduser().resolve() if raw else Path("/")


def find_rc_file(start: Path | None = None) -> Path | None:
    """Walk up from ``start`` (default: cwd) and return the closest .shellllmrc.

    Stops at ``$HOME`` inclusive — we never look in directories outside
    the user's home. Returns ``None`` if no file is found.
    """
    try:
        cur = (start or Path.cwd()).resolve()
    except (OSError, RuntimeError):
        return None
    home = _home()

    while True:
        candidate = cur / RC_NAME
        if candidate.is_file():
            return candidate
        if cur == home:
            return None
        parent = cur.parent
        if parent == cur:  # filesystem root
            return None
        # Stop once we leave $HOME — we don't read system-wide rc files.
        try:
            parent.relative_to(home)
        except ValueError:
            return None
        cur = parent


def read_rc_block() -> str:
    """Return the .shellllmrc contents rendered as a system block, or "".

    The file is read with errors='replace' so an encoding hiccup never
    crashes the call. Trailing-only files (just whitespace) render as
    "". Oversized files are tail-truncated with a marker so users know.
    """
    path = find_rc_file()
    if path is None:
        return ""

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""

    body = raw.strip()
    if not body:
        return ""

    truncated_note = ""
    if len(body) > MAX_RC_BYTES:
        body = body[-MAX_RC_BYTES:]
        truncated_note = f" (truncated to the last {MAX_RC_BYTES} bytes)"

    return (
        f"Project context (from {path}{truncated_note}). "
        f"Apply these conventions when proposing or answering:\n"
        f"---\n"
        f"{body}\n"
        f"---"
    )
