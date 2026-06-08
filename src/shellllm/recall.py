"""'???' — the memory layer: long-term facts + cross-session recall.

Three question marks reads as *"I'm trying to remember…"*. That's
exactly what this command does: search archived sessions, pin durable
facts, and inspect what shellllm knows about you across panes and
days. Everything that *isn't* "ask a question" or "propose a command"
lives here.

Shape
~~~~~

Bare query (most-used path) — implicit recall::

    ??? what was that grep flag again
    ??? ripgrep

Subcommands for fact management and explicit recall::

    ??? add <fact>        pin a long-term fact
    ??? list              list facts
    ??? drop <n>          drop fact #n
    ??? recall <query>    explicit recall (use this when the query
                          starts with a word that's also a subcommand)
    ??? status            counts
    ??? help

The "bare query vs subcommand" disambiguation is the only piece worth
spelling out: if the first arg is one of the known subcommand verbs
(``add``, ``list``, ``drop``, ``recall``, ``status``, ``help``), it's
a subcommand; otherwise the whole tail is a recall query. To search
for a literal subcommand word, use the explicit ``??? recall <word>``
form.
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


SUBCOMMANDS = frozenset({"add", "list", "drop", "recall", "status", "help"})

# Filter flags map a flag → the ``cmd`` field they restrict recall to.
# Add new entries here when a new asking surface is introduced.
_CMD_FILTERS: dict[str, str] = {
    "--ask": "ask",
    "--comma": "comma",
}


def _print_usage(label: str = "???") -> None:
    sys.stdout.write(
        f"usage: {label} <query>                  recall: search archive\n"
        f"       {label} --ask <query>            recall only `?` sessions\n"
        f"       {label} --comma <query>          recall only `,` sessions\n"
        f"       {label} add <fact>               save a long-term fact\n"
        f"       {label} list                     list saved facts\n"
        f"       {label} drop <n>                 drop fact #n\n"
        f"       {label} recall <query>           explicit recall (use when query\n"
        f"                                        starts with a subcommand word)\n"
        f"       {label} status                   show counts\n"
        f"       {label} help                     show this message\n"
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
        sys.stderr.write(f"{_RED}??? error:{_RESET} `add` needs a fact\n")
        return 2
    try:
        fact = memory.add(text)
    except ValueError as exc:
        sys.stderr.write(f"{_RED}??? error:{_RESET} {exc}\n")
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
        sys.stderr.write(f"{_RED}??? error:{_RESET} `drop` needs an index\n")
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


def _cmd_status(memory: MemoryStore, archive: Archive) -> int:
    facts = memory.load()
    print(f"{len(facts)} remembered facts · {archive.count()} archived sessions")
    return 0


def main() -> int:
    argv = list(sys.argv[1:])
    if not argv:
        _print_usage()
        return 0

    # Pull --ask / --comma off the front of argv so the rest is either a
    # bare query or a subcommand line. We only allow filter flags at
    # the start to keep the parser unambiguous: `??? add --ask foo`
    # would be confusing — does --ask filter the add? It doesn't.
    cmd_filter: str | None = None
    while argv and argv[0] in _CMD_FILTERS:
        flag = argv.pop(0)
        cmd_filter = _CMD_FILTERS[flag]

    if not argv:
        # Filter-only invocation: `??? --ask` with no query.
        sys.stderr.write(f"{_RED}??? error:{_RESET} no query after filter flag\n")
        return 2

    first, *rest = argv
    memory = MemoryStore()
    archive = Archive()

    if first in ("help", "--help", "-h"):
        _print_usage()
        return 0

    # Filter flags only make sense for recall paths. If the user
    # combined `--ask` with `add` / `list` / `drop` / `status`, that's
    # almost certainly a typo — facts are global, not per-command.
    if cmd_filter is not None and first in SUBCOMMANDS and first != "recall":
        sys.stderr.write(
            f"{_RED}??? error:{_RESET} filter flags only apply to recall, not `{first}`\n"
        )
        return 2

    # Bare query: first word isn't a known subcommand → treat the whole
    # tail as a recall query. This makes `??? what was that flag` work
    # without typing `recall` every time, which is the most-used path.
    if first not in SUBCOMMANDS:
        return _do_recall(archive, " ".join(argv), cmd_filter=cmd_filter)

    if first == "recall":
        return _do_recall(archive, " ".join(rest), cmd_filter=cmd_filter)
    if first == "add":
        return _cmd_add(memory, rest)
    if first == "list":
        return _cmd_list(memory)
    if first == "drop":
        return _cmd_drop(memory, rest)
    if first == "status":
        return _cmd_status(memory, archive)

    # Shouldn't get here — keeps mypy/pyright happy and gives a clean
    # message if a subcommand gets added to the set but not dispatched.
    sys.stderr.write(f"{_RED}??? error:{_RESET} unhandled subcommand {first!r}\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
