"""Long-term fact store — small, hand-rolled, swappable.

This is the persistence layer behind ``? --remember "<fact>"``. Facts
live in a flat JSONL file, get rendered as a ``<memory>`` block at the
top of the system prompt, and are global to the user (not per-pane) so
"the project I work on is X" carries across every terminal.

We keep the dependency surface zero so the offline-by-default story
holds. Once we have real cross-session usage we can swap the storage
layer for ``claude-mem`` or ``mem0`` behind the same ``MemoryStore``
interface.

Sizing
~~~~~~
Facts are capped two ways:
* A per-fact character cap to keep individual entries snappable.
* A whole-store cap so the system-prompt block stays small. When the
  cap is exceeded the oldest entries are spilled to a sibling archive
  file rather than being lost.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

MEMORY_FILE_ENV = "SHELLLM_MEMORY_FILE"
DEFAULT_MEMORY_FILE = Path.home() / ".cache" / "shellllm" / "memory.jsonl"

MAX_FACTS = 50
MAX_FACT_CHARS = 500


@dataclass(frozen=True)
class Fact:
    ts: float
    text: str

    def to_json(self) -> str:
        return json.dumps({"ts": self.ts, "text": self.text}, ensure_ascii=False)


def _resolve_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    override = os.environ.get(MEMORY_FILE_ENV)
    if override:
        return Path(override).expanduser()
    return DEFAULT_MEMORY_FILE


class MemoryStore:
    """File-backed list of facts. Concurrency model is "last write wins"."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = _resolve_path(path)
        self.archive_path = self.path.with_suffix(self.path.suffix + ".archive")

    def load(self) -> list[Fact]:
        if not self.path.exists():
            return []
        facts: list[Fact] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    blob = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = str(blob.get("text", "")).strip()
                if not text:
                    continue
                facts.append(Fact(ts=float(blob.get("ts", 0.0)), text=text))
        return facts

    def add(self, text: str, *, now: float | None = None) -> Fact:
        """Append a new fact. Trims long input, then enforces the size cap."""
        text = text.strip()
        if not text:
            raise ValueError("fact is empty")
        if len(text) > MAX_FACT_CHARS:
            text = text[: MAX_FACT_CHARS - 1] + "…"
        fact = Fact(ts=now if now is not None else time.time(), text=text)
        facts = self.load()
        facts.append(fact)
        self._enforce_cap_and_save(facts)
        return fact

    def forget(self, index: int) -> Fact | None:
        """Drop the fact at 1-based ``index`` (matches ``--memories`` display)."""
        facts = self.load()
        if index < 1 or index > len(facts):
            return None
        removed = facts.pop(index - 1)
        self._save(facts)
        return removed

    def clear(self) -> int:
        facts = self.load()
        self._save([])
        return len(facts)

    def _enforce_cap_and_save(self, facts: list[Fact]) -> None:
        """Trim to MAX_FACTS, archive the overflow, persist what's left."""
        if len(facts) > MAX_FACTS:
            overflow = facts[: len(facts) - MAX_FACTS]
            facts = facts[len(facts) - MAX_FACTS :]
            self._append_archive(overflow)
        self._save(facts)

    def _save(self, facts: list[Fact]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for fact in facts:
                f.write(fact.to_json())
                f.write("\n")
        tmp.replace(self.path)

    def _append_archive(self, facts: list[Fact]) -> None:
        self.archive_path.parent.mkdir(parents=True, exist_ok=True)
        with self.archive_path.open("a", encoding="utf-8") as f:
            for fact in facts:
                f.write(fact.to_json())
                f.write("\n")


def render_memory_block(facts: list[Fact]) -> str:
    """Render a system-prompt section. Returns empty string if no facts."""
    if not facts:
        return ""
    lines = ["<memory>", "Facts the user has asked you to remember:"]
    for fact in facts:
        lines.append(f"- {fact.text}")
    lines.append("</memory>")
    return "\n".join(lines)
