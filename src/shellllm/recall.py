"""'???' — the memory layer: long-term facts + cross-session recall.

Three question marks reads as *"I'm trying to remember…"*. That's
exactly what this command does: search archived sessions, pin durable
facts, and inspect what shellllm knows about you across panes and
days. Everything that *isn't* "ask a question" or "propose a command"
lives here.

Shape
~~~~~

Bare query is the most-used path — implicit recall::

    ??? what was that grep flag again
    ??? ripgrep

Every other operation is a flag — no bare-word verbs::

    ??? --add <fact>          pin a long-term fact
    ??? --list                list facts
    ??? --drop <n>            drop fact #n
    ??? --status              counts (facts + archives)
    ??? --help                usage

    ??? --ask <query>         recall only `?` sessions
    ??? --comma <query>       recall only `,` sessions

Mode flags (``--add`` / ``--list`` / ``--drop`` / ``--status``) are
mutually exclusive. Filter flags (``--ask`` / ``--comma``) only make
sense alongside a recall query — combining them with a mode flag
errors, because facts are global and counts are global.
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


# Mutually-exclusive "do this instead of recall" flags.
_MODE_FLAGS = ("--add", "--list", "--drop", "--status")

# Restrict recall to a single asking surface. Extend when a new asking
# command is added.
_FILTER_FLAGS: dict[str, str] = {
    "--ask": "ask",
    "--comma": "comma",
}


def _print_usage(label: str = "???") -> None:
    sys.stdout.write(
        f"usage: {label} <query>                  recall: search archive\n"
        f"       {label} --ask <query>            recall only `?` sessions\n"
        f"       {label} --comma <query>          recall only `,` sessions\n"
        f"       {label} --add <fact>             save a long-term fact\n"
        f"       {label} --list                   list saved facts\n"
        f"       {label} --drop <n>               drop fact #n\n"
        f"       {label} --status                 show counts\n"
        f"       {label} --help                   show this message\n"
    )


def _format_recall_hit(idx: int, hit) -> str:
    when = datetime.fromtimestamp(hit.archived_at).strftime("%Y-%m-%d %H:%M")
    parts = [
        f"{_DIM}#{idx:<2}{_RESET}",
        f"{_CYAN}{hit.cmd}{_RESET}",
        f"{_DIM}{when}{_RESET}",
    ]
    if hit.last_pwd:
        parts.append(f"{_DIM}{hit.last_pwd}{_RESET}")
    header = " · ".join(parts)
    return f"{header}\n  {hit.snippet}\n\n"


def _do_recall(archive: Archive, query: str, *, cmd_filter: str | None = None) -> int:
    query = query.strip()
    if not query:
        sys.stderr.write(f"{_RED}??? error:{_RESET} recall needs a query\n")
        return 2
    hits = archive.search(
        query,
        limit=10,
        query_embedding=_safe_embed(query),
        cmd_filter=cmd_filter,
    )
    if not hits:
        scope = f" in `{cmd_filter}` sessions" if cmd_filter else ""
        print(f"(no archive hits for {query!r}{scope})")
        return 0
    for i, hit in enumerate(hits, 1):
        sys.stdout.write(_format_recall_hit(i, hit))
    return 0


def _cmd_add(memory: MemoryStore, rest: list[str]) -> int:
    text = " ".join(rest).strip()
    if not text:
        sys.stderr.write(f"{_RED}??? error:{_RESET} `--add` needs a fact\n")
        return 2
    try:
        fact = memory.add(text)
    except ValueError as exc:
        sys.stderr.write(f"{_RED}??? error:{_RESET} {exc}\n")
        return 2
    print(f"remembered: {fact.text}")
    return 0


def _cmd_list(memory: MemoryStore, rest: list[str]) -> int:
    if rest:
        sys.stderr.write(f"{_RED}??? error:{_RESET} `--list` takes no arguments\n")
        return 2
    facts = memory.load()
    if not facts:
        print("(no remembered facts)")
        return 0
    for i, fact in enumerate(facts, 1):
        print(f"{i:>2}. {fact.text}")
    return 0


def _cmd_drop(memory: MemoryStore, rest: list[str]) -> int:
    if not rest:
        sys.stderr.write(f"{_RED}??? error:{_RESET} `--drop` needs an index\n")
        return 2
    if len(rest) > 1:
        sys.stderr.write(f"{_RED}??? error:{_RESET} `--drop` takes one index\n")
        return 2
    try:
        idx = int(rest[0])
    except ValueError:
        sys.stderr.write(f"{_RED}??? error:{_RESET} index must be an integer\n")
        return 2
    removed = memory.forget(idx)
    if removed is None:
        sys.stderr.write(f"{_RED}??? error:{_RESET} no fact at index {idx}\n")
        return 2
    print(f"forgot: {removed.text}")
    return 0


def _cmd_status(memory: MemoryStore, archive: Archive, rest: list[str]) -> int:
    if rest:
        sys.stderr.write(f"{_RED}??? error:{_RESET} `--status` takes no arguments\n")
        return 2
    facts = memory.load()
    print(f"{len(facts)} remembered facts · {archive.count()} archived sessions")
    return 0


def _collect_mode(argv: list[str]) -> tuple[str | None, int]:
    """Pull a single mode flag from ``argv``. Multi-mode → error code."""
    present = [f for f in _MODE_FLAGS if f in argv]
    if len(present) > 1:
        sys.stderr.write(
            f"{_RED}??? error:{_RESET} only one of {', '.join(_MODE_FLAGS)} at a time\n"
        )
        return None, 2
    if present:
        argv.remove(present[0])
        return present[0], 0
    return None, 0


def _collect_filter(argv: list[str]) -> tuple[str | None, int]:
    """Pull a single filter flag from ``argv``. Multi-filter → error code."""
    present = [f for f in _FILTER_FLAGS if f in argv]
    if len(present) > 1:
        sys.stderr.write(
            f"{_RED}??? error:{_RESET} only one of {', '.join(_FILTER_FLAGS)} at a time\n"
        )
        return None, 2
    if present:
        argv.remove(present[0])
        return _FILTER_FLAGS[present[0]], 0
    return None, 0


def main() -> int:
    argv = list(sys.argv[1:])
    if not argv or argv[0] in ("--help", "-h"):
        _print_usage()
        return 0

    mode, err = _collect_mode(argv)
    if err:
        return err

    cmd_filter, err = _collect_filter(argv)
    if err:
        return err

    memory = MemoryStore()
    archive = Archive()

    # Filter flags only apply to recall paths. Combining with a mode
    # flag is almost certainly a typo — facts and counts are global.
    if cmd_filter is not None and mode is not None:
        sys.stderr.write(
            f"{_RED}??? error:{_RESET} filter flags only apply to recall, not `{mode}`\n"
        )
        return 2

    if mode == "--add":
        return _cmd_add(memory, argv)
    if mode == "--list":
        return _cmd_list(memory, argv)
    if mode == "--drop":
        return _cmd_drop(memory, argv)
    if mode == "--status":
        return _cmd_status(memory, archive, argv)

    # No mode flag → recall path. Filter is optional.
    if not argv:
        sys.stderr.write(f"{_RED}??? error:{_RESET} no query\n")
        return 2
    return _do_recall(archive, " ".join(argv), cmd_filter=cmd_filter)


if __name__ == "__main__":
    raise SystemExit(main())
