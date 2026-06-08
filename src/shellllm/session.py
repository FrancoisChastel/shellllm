"""Per-terminal conversation persistence for ``?`` and ``???``.

Each terminal pane gets its own JSONL session file. The pane identity is
derived in priority order from ``TERM_SESSION_ID`` (set by Terminal.app
and iTerm2), ``TMUX_PANE``, or the parent shell PID — the first one that
exists is hashed and used as the session id. That mapping gives a single
identifier that stays stable as long as the same shell process is
serving the same tab/pane, and rotates when you open a fresh one.

A session is **per command**, so ``ask`` and ``search`` keep independent
threads in the same pane — sharing them would conflate two different
conversational modes.

On disk
~~~~~~~
::

    ~/.cache/shellllm/sessions/{cmd}-{id}.jsonl

The first line is a metadata header (``{"_meta": ...}``); subsequent
lines are chat-format message dicts. Append-only at runtime; reset
rewrites the whole file.

Idle TTL
~~~~~~~~
If the session was last touched more than ``IDLE_TTL_SECONDS`` ago we
rotate it (rename to ``<file>.expired-<ts>``) and start fresh. The TTL
is the main guard against context bleed when you come back to a long-
ignored tab.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from .archive import Archive

    Embedder = Callable[[str], "list[float] | None"]

IDLE_TTL_SECONDS = 30 * 60  # 30 minutes
SESSIONS_DIR_ENV = "SHELLLM_SESSIONS_DIR"


def _default_sessions_dir() -> Path:
    override = os.environ.get(SESSIONS_DIR_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".cache" / "shellllm" / "sessions"


def derive_terminal_id() -> str:
    """Return a 12-char id stable for the lifetime of this terminal pane.

    Falls back through TERM_SESSION_ID → TMUX_PANE → PPID → PID. The
    final ``str(os.getpid())`` fallback exists only so tests and odd
    environments can't fail to produce *something*; in any real shell
    PPID is set.
    """
    raw = (
        os.environ.get("TERM_SESSION_ID")
        or os.environ.get("TMUX_PANE")
        or os.environ.get("WINDOWID")
        or str(os.getppid())
        or str(os.getpid())
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


@dataclass
class SessionMeta:
    """Mutable session metadata. Persists as the first JSONL line."""

    created: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)
    last_pwd: str = ""
    last_date: str = ""
    turn_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "_meta": {
                "created": self.created,
                "last_used": self.last_used,
                "last_pwd": self.last_pwd,
                "last_date": self.last_date,
                "turn_count": self.turn_count,
            }
        }

    @classmethod
    def from_dict(cls, blob: dict[str, Any]) -> SessionMeta:
        m = blob.get("_meta", {})
        return cls(
            created=float(m.get("created", time.time())),
            last_used=float(m.get("last_used", time.time())),
            last_pwd=str(m.get("last_pwd", "")),
            last_date=str(m.get("last_date", "")),
            turn_count=int(m.get("turn_count", 0)),
        )


@dataclass
class SessionStore:
    """A single conversation thread bound to one terminal pane and command."""

    cmd: str
    terminal_id: str
    path: Path
    meta: SessionMeta
    messages: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def open(
        cls,
        cmd: str,
        *,
        terminal_id: str | None = None,
        sessions_dir: Path | None = None,
        idle_ttl: int = IDLE_TTL_SECONDS,
        now: float | None = None,
        archive: Archive | None = None,
        embed_fn: Embedder | None = None,
    ) -> tuple[SessionStore, bool]:
        """Load or initialize a session. Returns ``(store, expired)``.

        ``expired=True`` means the previous file was past TTL and was
        rotated; the caller may want to surface that on stderr so the
        user understands why the context starts fresh. When ``archive``
        is provided, an expiring transcript is first ingested into the
        recall store; ``embed_fn`` is an optional callable that turns
        the flattened transcript into an embedding for vector search.
        """
        directory = sessions_dir or _default_sessions_dir()
        directory.mkdir(parents=True, exist_ok=True)
        tid = terminal_id or derive_terminal_id()
        path = directory / f"{cmd}-{tid}.jsonl"
        clock = now if now is not None else time.time()

        if not path.exists():
            return (
                cls(
                    cmd=cmd,
                    terminal_id=tid,
                    path=path,
                    meta=SessionMeta(created=clock, last_used=clock),
                ),
                False,
            )

        meta, messages = _read_jsonl(path)
        if clock - meta.last_used > idle_ttl:
            if archive is not None and messages:
                archive.ingest_session(
                    cmd=cmd,
                    terminal_id=tid,
                    created_at=meta.created,
                    last_used=meta.last_used,
                    last_pwd=meta.last_pwd,
                    last_date=meta.last_date,
                    turn_count=meta.turn_count,
                    messages=messages,
                    embed_fn=embed_fn,
                    archived_at=clock,
                )
            _rotate(path, clock)
            return (
                cls(
                    cmd=cmd,
                    terminal_id=tid,
                    path=path,
                    meta=SessionMeta(created=clock, last_used=clock),
                ),
                True,
            )

        return (
            cls(
                cmd=cmd,
                terminal_id=tid,
                path=path,
                meta=meta,
                messages=messages,
            ),
            False,
        )

    def is_empty(self) -> bool:
        return not self.messages

    def reset(self) -> None:
        """Wipe the on-disk session. Caller starts a fresh in-memory store."""
        if self.path.exists():
            self.path.unlink()
        self.messages = []
        self.meta = SessionMeta()

    def archive_and_reset(
        self,
        *,
        archive: Archive | None = None,
        embed_fn: Embedder | None = None,
        now: float | None = None,
    ) -> None:
        """Ingest into ``archive`` (if given) then rotate the JSONL aside.

        Without ``archive`` this matches the old behaviour: rename the
        on-disk file so the next ``--new`` starts cold.
        """
        clock = now if now is not None else time.time()
        if archive is not None and self.messages:
            archive.ingest_session(
                cmd=self.cmd,
                terminal_id=self.terminal_id,
                created_at=self.meta.created,
                last_used=self.meta.last_used,
                last_pwd=self.meta.last_pwd,
                last_date=self.meta.last_date,
                turn_count=self.meta.turn_count,
                messages=self.messages,
                embed_fn=embed_fn,
                archived_at=clock,
            )
        if self.path.exists():
            _rotate(self.path, clock)
        self.messages = []
        self.meta = SessionMeta()

    def write(self) -> None:
        """Persist meta + all messages atomically (write-then-rename)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            f.write(json.dumps(self.meta.to_dict(), ensure_ascii=False))
            f.write("\n")
            for msg in self.messages:
                f.write(json.dumps(msg, ensure_ascii=False))
                f.write("\n")
        tmp.replace(self.path)

    def extend(self, new_messages: list[dict[str, Any]]) -> None:
        self.messages.extend(new_messages)

    def touch(self, *, pwd: str, date: str, now: float | None = None) -> None:
        self.meta.last_used = now if now is not None else time.time()
        self.meta.last_pwd = pwd
        self.meta.last_date = date
        self.meta.turn_count += 1


def _read_jsonl(path: Path) -> tuple[SessionMeta, list[dict[str, Any]]]:
    """Parse a session file. Tolerates a corrupt line by skipping it."""
    meta = SessionMeta()
    messages: list[dict[str, Any]] = []
    first = True
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                blob = json.loads(line)
            except json.JSONDecodeError:
                continue
            if first and isinstance(blob, dict) and "_meta" in blob:
                meta = SessionMeta.from_dict(blob)
                first = False
                continue
            first = False
            if isinstance(blob, dict) and "role" in blob:
                messages.append(blob)
    return meta, messages


def _rotate(path: Path, now: float) -> None:
    """Move an expired or archived file aside, preserving its content."""
    ts = int(now)
    rotated = path.with_suffix(path.suffix + f".expired-{ts}")
    try:
        path.rename(rotated)
    except OSError:
        # Best-effort: if rename fails (e.g. cross-device), drop the file.
        path.unlink(missing_ok=True)


def sweep_expired(
    sessions_dir: Path | None = None,
    *,
    max_age_seconds: int = 7 * 24 * 3600,
    now: float | None = None,
) -> int:
    """Janitor: delete rotated session files older than ``max_age_seconds``.

    Called opportunistically on session open so the cache doesn't grow
    forever in pane-heavy workflows.
    """
    directory = sessions_dir or _default_sessions_dir()
    if not directory.exists():
        return 0
    cutoff = (now if now is not None else time.time()) - max_age_seconds
    removed = 0
    for p in directory.glob("*.expired-*"):
        try:
            if p.stat().st_mtime < cutoff:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed
