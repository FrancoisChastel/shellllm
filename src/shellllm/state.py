"""'?:' — the meta command. Long-term facts + cross-session recall.

Everything that *isn't* "ask a question" or "propose a command" lives
here. By peeling state ops off ``?`` and ``,`` we keep those punchy and
ask-only; ``?:`` owns the verbs that mutate or query the durable layer.

Subcommands
~~~~~~~~~~~

* ``?: add <fact>``     — pin a long-term fact (was ``? --remember``)
* ``?: list``           — list facts                (was ``? --memories``)
* ``?: drop <n>``       — drop fact #n              (was ``? --forget``)
* ``?: recall <q>``     — search archived sessions  (was ``? --recall``)
* ``?: status``         — print quick counts
* ``?: help``           — show usage

The CLI deliberately uses bare verbs (``add``, ``list``, ``drop`` …)
rather than flag soup so the surface stays tiny and teachable. Bare
``?:`` with no arguments prints help.
"""

from __future__ import annotations

import sys
from datetime import datetime

from .archive import Archive
from .embed import embed as embed_text
from .memory import MemoryStore

_DIM = "\x1b[2m"
_CYAN = "\x1b[36m"
_RED = "\x1b[31m"
_RESET = "\x1b[0m"


def _safe_embed(text: str) -> list[float] | None:
    """Best-effort embedding; never propagates exceptions."""
    try:
        return embed_text(text)
    except Exception:  # noqa: BLE001
        return None


def _print_usage(label: str = "?:") -> None:
    sys.stdout.write(
        f"usage: {label} <subcommand> [args]\n"
        f"  {label} add <fact>         save a long-term fact\n"
        f"  {label} list               list saved facts\n"
        f"  {label} drop <n>           drop fact #n\n"
        f"  {label} recall <query>     search archived sessions\n"
        f"  {label} status             show counts (facts + archives)\n"
        f"  {label} help               show this message\n"
    )


def _format_recall_hit(idx: int, hit) -> str:
    when = datetime.fromtimestamp(hit.archived_at).strftime("%Y-%m-%d %H:%M")
    header_parts = [
        f"{_DIM}#{idx:<2}{_RESET}",
        f"{_CYAN}{hit.cmd}{_RESET}",
        f"{_DIM}{when}{_RESET}",
    ]
    if hit.last_pwd:
        header_parts.append(f"{_DIM}{hit.last_pwd}{_RESET}")
    header = " · ".join(header_parts)
    return f"{header}\n  {hit.snippet}\n\n"


def _cmd_add(memory: MemoryStore, rest: list[str]) -> int:
    text = " ".join(rest).strip()
    if not text:
        sys.stderr.write(f"{_RED}?: error:{_RESET} `add` needs a fact\n")
        return 2
    try:
        fact = memory.add(text)
    except ValueError as exc:
        sys.stderr.write(f"{_RED}?: error:{_RESET} {exc}\n")
        return 2
    print(f"remembered: {fact.text}")
    return 0


def _cmd_list(memory: MemoryStore) -> int:
    facts = memory.load()
    if not facts:
        print("(no remembered facts)")
        return 0
    for i, fact in enumerate(facts, 1):
        print(f"{i:>2}. {fact.text}")
    return 0


def _cmd_drop(memory: MemoryStore, rest: list[str]) -> int:
    if not rest:
        sys.stderr.write(f"{_RED}?: error:{_RESET} `drop` needs an index\n")
        return 2
    try:
        idx = int(rest[0])
    except ValueError:
        sys.stderr.write(f"{_RED}?: error:{_RESET} index must be an integer\n")
        return 2
    removed = memory.forget(idx)
    if removed is None:
        sys.stderr.write(f"{_RED}?: error:{_RESET} no fact at index {idx}\n")
        return 2
    print(f"forgot: {removed.text}")
    return 0


def _cmd_recall(archive: Archive, rest: list[str]) -> int:
    query = " ".join(rest).strip()
    if not query:
        sys.stderr.write(f"{_RED}?: error:{_RESET} `recall` needs a query\n")
        return 2
    hits = archive.search(query, limit=10, query_embedding=_safe_embed(query))
    if not hits:
        print(f"(no archive hits for {query!r})")
        return 0
    for i, hit in enumerate(hits, 1):
        sys.stdout.write(_format_recall_hit(i, hit))
    return 0


def _cmd_status(memory: MemoryStore, archive: Archive) -> int:
    facts = memory.load()
    print(f"{len(facts)} remembered facts · {archive.count()} archived sessions")
    return 0


SUBCOMMANDS = {
    "add": "save a long-term fact",
    "list": "list saved facts",
    "drop": "drop fact #n",
    "recall": "search archived sessions",
    "status": "show counts",
    "help": "show this message",
}


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("help", "--help", "-h"):
        _print_usage()
        return 0 if argv else 0

    subcommand, *rest = argv
    memory = MemoryStore()
    archive = Archive()

    if subcommand == "add":
        return _cmd_add(memory, rest)
    if subcommand == "list":
        return _cmd_list(memory)
    if subcommand == "drop":
        return _cmd_drop(memory, rest)
    if subcommand == "recall":
        return _cmd_recall(archive, rest)
    if subcommand == "status":
        return _cmd_status(memory, archive)

    sys.stderr.write(f"{_RED}?: error:{_RESET} unknown subcommand {subcommand!r}\n")
    _print_usage()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
