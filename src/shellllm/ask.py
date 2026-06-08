"""'?' — answer a question with a narrow read-only agent. Streams markdown.

Three tools, all read-only:
  - read_file(path): goes through safe_fs (hard wall + denylist)
  - web_search(query): top 3 DuckDuckGo results, snippet text only
  - fetch_url(url): follow one of those links and read the page

Streamed answer renders as markdown via rich.live.Live, refreshing as
chunks arrive. Tool-call traces and errors go to stderr so a `… | less`
or `… > out.md` sees only the answer.

Multi-turn
~~~~~~~~~~
Each terminal pane gets a sticky session (see :mod:`shellllm.session`).
``?`` always continues whatever is pinned to this pane, unless an idle
TTL has elapsed (auto-reset) or the user passes ``--new``. The prelude
(date / OS / PWD) is re-injected only on resume or when the working
directory or wall-clock date have changed, so steady-state conversation
inside a single pane stays cheap.

The agent loop is exported as ``run_agent`` so the ``???`` search command
(``shellllm.search``) can reuse it with a different system prompt and
its own per-pane session bucket.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from .archive import Archive, render_hits_block
from .claude_mem import ClaudeMemAdapter, hint_on_first_use, render_context_block
from .client import LlamaServerError, chat, chat_stream
from .compact import (
    DEFAULT_CTX_TOKENS,
    build_summary_prompt,
    compact,
    estimate_tokens,
)
from .context import build_prelude
from .embed import embed as embed_text
from .memory import MemoryStore, render_memory_block
from .safe_fs import WallViolation, safe_read_text
from .session import SessionStore, sweep_expired
from .web import fetch_url_as_text, search_as_text

MAX_ITERATIONS = 12

# Per-tool-result cap before injection into the message history. Sized to
# absorb the full ``fetch_url`` payload (8000 chars) plus its truncation
# footer without further chopping.
MAX_TOOL_RESULT_CHARS = 10_000

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a regular file inside $HOME or $PWD. Refuses files in "
                ".ssh, .aws, .gnupg, .kube, keychains, and other secret "
                "directories. Returns up to 1MB of text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative path. ~ and $VARS are expanded.",
                    }
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web. Returns up to 3 results with title, URL, and "
                "snippet. Follow up with `fetch_url` when a snippet is too "
                "thin to answer from."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": (
                "Fetch an http(s) URL and return the page as readable plain "
                "text (HTML stripped). Use this to follow a link from "
                "`web_search` when the snippet isn't enough. Refuses "
                "private/local addresses. Returns up to 8000 characters."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute http(s) URL.",
                    },
                },
                "required": ["url"],
            },
        },
    },
]

ASK_SYSTEM = (
    "You answer the user's question. You have three tools, all read-only: "
    "`read_file` for files in $HOME or $PWD, `web_search` for web lookups, "
    "and `fetch_url` to follow a search result into its actual page. Call "
    "them only when you need to — many questions you can answer directly. "
    "When you do search, follow the most promising link with `fetch_url` "
    "rather than answering from snippets alone. Format your answer in "
    "concise markdown and cite the URLs you used. If a tool refuses (e.g. "
    "`WallViolation`), respect the refusal and reason from what you have."
)

# Back-compat alias for any external importers.
SYSTEM = ASK_SYSTEM

# ANSI: dim grey for tool traces, reset, bright red for tool errors.
_DIM = "\x1b[2m"
_RED = "\x1b[31m"
_CYAN = "\x1b[36m"
_RESET = "\x1b[0m"

_stdout_console = Console()
_err_console = Console(stderr=True)


def _dispatch(name: str, args: dict[str, Any]) -> str:
    if name == "read_file":
        try:
            return safe_read_text(args["path"])
        except WallViolation as exc:
            return f"WallViolation: {exc}"
        except (OSError, KeyError, UnicodeError) as exc:
            return f"error: {exc.__class__.__name__}: {exc}"
    if name == "web_search":
        try:
            return search_as_text(args["query"])
        except KeyError:
            return "error: missing 'query'"
    if name == "fetch_url":
        try:
            return fetch_url_as_text(args["url"])
        except KeyError:
            return "error: missing 'url'"
    return f"error: unknown tool {name!r}"


def _trace(label: str, body: str) -> None:
    sys.stderr.write(f"{_DIM}· {label}({body}){_RESET}\n")
    sys.stderr.flush()


def _note(text: str) -> None:
    """One-line dim cyan hint on stderr — for resume / compaction signals."""
    sys.stderr.write(f"{_DIM}{_CYAN}↻ {text}{_RESET}\n")
    sys.stderr.flush()


def _stream_round_markdown(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Stream one round into a live-rendered markdown region.

    Returns (full_text, tool_calls). The Live region stays visible after
    exit, so subsequent rounds append below it in the terminal.
    """
    full_text = ""
    tool_calls: list[dict[str, Any]] = []

    with Live(
        Markdown(""),
        console=_stdout_console,
        refresh_per_second=10,
        vertical_overflow="visible",
    ) as live:
        for event in chat_stream(messages, tools=TOOLS, max_tokens=1500):
            if event["type"] == "content":
                full_text += event["text"]
                live.update(Markdown(full_text))
            elif event["type"] == "done":
                tool_calls = event["tool_calls"]

    return full_text, tool_calls


def _stream_round_plain(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Non-TTY path: stream raw text, no Live rendering."""
    full_text = ""
    tool_calls: list[dict[str, Any]] = []
    for event in chat_stream(messages, tools=TOOLS, max_tokens=1500):
        if event["type"] == "content":
            sys.stdout.write(event["text"])
            sys.stdout.flush()
            full_text += event["text"]
        elif event["type"] == "done":
            tool_calls = event["tool_calls"]
    if full_text and not full_text.endswith("\n"):
        sys.stdout.write("\n")
        sys.stdout.flush()
    return full_text, tool_calls


def _summarize_via_model(slice_: list[dict[str, Any]]) -> str:
    """Summarizer callback for ``compact()`` — uses the local model itself."""
    from .compact import SUMMARY_MAX_TOKENS  # local import to keep top clean

    msg = chat(build_summary_prompt(slice_), max_tokens=SUMMARY_MAX_TOKENS)
    return str(msg.get("content") or "").strip()


def _maybe_compact(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Run compaction if estimated tokens cross the trigger; report on stderr."""
    new_messages, report = compact(messages, _summarize_via_model)
    if report.triggered:
        _note(
            f"compacted {report.summarized_messages} older messages: "
            f"{report.before_tokens}→{report.after_tokens} tokens"
        )
    if report.over_hard_cap:
        sys.stderr.write(
            f"{_RED}context near cap ({report.after_tokens}/{DEFAULT_CTX_TOKENS} "
            f"tokens). Run `? --reset` if quality drops.{_RESET}\n"
        )
    return new_messages


def _prelude_changed(meta_pwd: str, meta_date: str, pwd: str, date: str) -> bool:
    return meta_pwd != pwd or meta_date != date


def run_agent(
    user_prompt: str,
    *,
    system: str = ASK_SYSTEM,
    include_context: bool = True,
    session: SessionStore | None = None,
    resumed: bool = False,
    memory: MemoryStore | None = None,
    claude_mem: ClaudeMemAdapter | None = None,
    cmd_label: str = "ask",
    archive: Archive | None = None,
    auto_recall: bool = False,
) -> int:
    """Run the tool-calling agent loop. Returns a process-style exit code.

    Exposed so other entry points (e.g. ``shellllm-search``) can reuse the
    same dispatch + streaming machinery with their own system prompt.

    Multi-turn behaviour
    --------------------
    When ``session`` is provided the loop is conversational: prior turns
    are loaded, the new turn is appended, the result is persisted.

    Prelude injection follows the project rule "only when it actually
    matters": on first turn, on resume, or when ``$PWD`` / wall-clock
    date changed since the last turn. Otherwise the model already knows
    those facts from history.
    """
    from pathlib import Path

    pwd = str(Path.cwd())
    date = datetime.now().astimezone().strftime("%Y-%m-%d")

    # Live's redraw uses cursor positioning, which only makes sense on a
    # TTY. Piped/redirected stdout falls back to plain streaming.
    stream_round = _stream_round_markdown if sys.stdout.isatty() else _stream_round_plain

    # Session stores conversation only (user/assistant/tool + any
    # summary-so-far system message produced by compaction). The static
    # system layer — memory + agent rules + maybe-a-prelude — is rebuilt
    # fresh every turn so adding a fact or changing PWD takes effect
    # immediately.
    history: list[dict[str, Any]] = list(session.messages) if session else []
    first_turn = not history

    system_msgs: list[dict[str, Any]] = []
    facts = memory.load() if memory else []
    facts_block = render_memory_block(facts)
    if facts_block:
        system_msgs.append({"role": "system", "content": facts_block})
    system_msgs.append({"role": "system", "content": system})

    if include_context and (
        first_turn
        or resumed
        or _prelude_changed(
            session.meta.last_pwd if session else "",
            session.meta.last_date if session else "",
            pwd,
            date,
        )
    ):
        system_msgs.append({"role": "system", "content": build_prelude()})

    # claude-mem context injection: only on a brand-new session, so we
    # don't repeatedly re-paste the same prior observations as the
    # conversation grows. The model already has them in history after
    # turn one.
    if first_turn and claude_mem is not None and claude_mem.enabled:
        hint_on_first_use(claude_mem)
        context_text = claude_mem.query_context(user_prompt)
        if context_text:
            block = render_context_block(context_text)
            if block:
                system_msgs.append({"role": "system", "content": block})

    # Local recall (always on opt-in): scan the archive for snippets
    # that look related to this question and inject them. Same first-
    # turn-only logic as claude-mem so we don't repeat ourselves.
    if first_turn and archive is not None and auto_recall:
        recall_block = _build_recall_block(archive, user_prompt)
        if recall_block:
            system_msgs.append({"role": "system", "content": recall_block})
            _note("recall: injected snippets from earlier sessions")

    if resumed and session is not None:
        _note(
            f"continuing — turn {session.meta.turn_count + 1}, "
            f"~{estimate_tokens(system_msgs + history)} tokens"
        )

    user_message: dict[str, Any] = {"role": "user", "content": user_prompt}
    messages: list[dict[str, Any]] = system_msgs + history + [user_message]
    messages = _maybe_compact(messages)

    # After compaction, the history portion may have changed (a summary
    # system message replaced older turns). Recompute the slice we'll
    # persist by stripping the rebuildable system prefix.
    new_history_with_user = _strip_leading_system(messages, expected=len(system_msgs))

    for _ in range(MAX_ITERATIONS):
        full_text, tool_calls = stream_round(messages)

        if not tool_calls:
            assistant_msg = {"role": "assistant", "content": full_text}
            messages.append(assistant_msg)
            new_history_with_user.append(assistant_msg)
            if session is not None:
                session.messages = new_history_with_user
                session.touch(pwd=pwd, date=date)
                session.write()
            _record_turn_observation(
                claude_mem,
                cmd_label=cmd_label,
                user_prompt=user_prompt,
                answer=full_text,
                pwd=pwd,
                date=date,
            )
            return 0

        assistant_msg = {
            "role": "assistant",
            "content": full_text,
            "tool_calls": tool_calls,
        }
        messages.append(assistant_msg)
        new_history_with_user.append(assistant_msg)

        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            _trace(name, json.dumps(args)[:120])
            result = _dispatch(name, args)
            tool_msg = {
                "role": "tool",
                "tool_call_id": call["id"],
                "content": result[:MAX_TOOL_RESULT_CHARS],
            }
            messages.append(tool_msg)
            new_history_with_user.append(tool_msg)

    sys.stderr.write(f"{_RED}(stopped: hit tool-call iteration limit){_RESET}\n")
    if session is not None:
        session.messages = new_history_with_user
        session.touch(pwd=pwd, date=date)
        session.write()
    return 1


def _safe_embed(text: str) -> list[float] | None:
    """Wrap :func:`shellllm.embed.embed` so a missing server never propagates."""
    try:
        return embed_text(text)
    except Exception:  # noqa: BLE001
        return None


def _build_recall_block(archive: Archive, query: str, *, limit: int = 3) -> str:
    """Run a hybrid search against the archive and return a system block.

    Returns the empty string when there's nothing useful — the caller
    should treat that as "no injection".
    """
    query_vec = _safe_embed(query)
    hits = archive.search(query, limit=limit, query_embedding=query_vec)
    return render_hits_block(hits)


def _format_recall_hit(idx: int, hit: Any) -> str:
    """One-line-ish stdout rendering for ``? --recall`` results."""
    from datetime import datetime

    when = datetime.fromtimestamp(hit.archived_at).strftime("%Y-%m-%d %H:%M")
    parts = [
        f"{_DIM}#{idx:<2}{_RESET}",
        f"{_CYAN}{hit.cmd}{_RESET}",
        f"{_DIM}{when}{_RESET}",
    ]
    if hit.last_pwd:
        parts.append(f"{_DIM}{hit.last_pwd}{_RESET}")
    header = " · ".join(parts)
    return f"{header}\n  {hit.snippet}\n"


def _record_turn_observation(
    adapter: ClaudeMemAdapter | None,
    *,
    cmd_label: str,
    user_prompt: str,
    answer: str,
    pwd: str,
    date: str,
) -> None:
    """Push a compact narrative of this turn to claude-mem (if enabled).

    The format is intentionally short — claude-mem already runs its own
    distillation pass. We just give it a useful seed.
    """
    if adapter is None or not adapter.enabled:
        return
    answer_short = answer.strip()
    if len(answer_short) > 1200:
        answer_short = answer_short[:1200].rstrip() + "…"
    narrative = (
        f"shellllm `{cmd_label}` turn in {pwd} on {date}:\n"
        f"Q: {user_prompt.strip()}\n"
        f"A: {answer_short}"
    )
    adapter.record_observation_async(
        narrative,
        kind="shellllm-turn",
        metadata={"command": cmd_label, "pwd": pwd, "date": date},
    )


def _strip_leading_system(messages: list[dict[str, Any]], *, expected: int) -> list[dict[str, Any]]:
    """Return ``messages`` minus the head of system messages we just injected.

    After compaction the head may contain extra system messages (notably
    a ``<summary-so-far>`` block), and we want to keep those — they ARE
    part of the persisted history. We only want to drop the
    just-prepended memory/rules/prelude block we built for this call.
    """
    return messages[expected:] if expected else list(messages)


# Avoid an unused import lint warning in callers.
__all__ = [
    "ASK_SYSTEM",
    "SYSTEM",
    "MAX_ITERATIONS",
    "MAX_TOOL_RESULT_CHARS",
    "TOOLS",
    "main",
    "run_agent",
    "run_cli",
]


def _print_usage(label: str, *, to: Any = None) -> None:
    """Write the flag reference to stdout (default) or a given stream."""
    out = to or sys.stdout
    out.write(
        f"usage: {label} <question>\n"
        f"       {label} --new <question>          start a fresh session\n"
        f"       {label} --reset                   drop current session\n"
        f"       {label} --history                 print session transcript\n"
        f"       {label} --compact                 force compaction\n"
        f"       {label} --auto-recall <q>         inject archive hits as context this turn\n"
        f"       {label} --no-auto-recall <q>     skip recall this turn (override env)\n"
        f"       {label} --mem | --no-mem         force claude-mem on/off for this call\n"
        f"       {label} --help                    show this message\n"
        f"\n"
        f"For facts and cross-session recall, see `?: help`.\n"
    )


# Flags that used to live on `?` / `???` and have moved to `?:`. We keep
# matching them so a stale muscle-memory invocation gets a clear redirect
# instead of falling through to the model as a regular question.
_MOVED_FLAGS: dict[str, str] = {
    "--remember": "?: add <fact>",
    "--memories": "?: list",
    "--forget": "?: drop <n>",
    "--recall": "?: recall <query>",
}


def _check_moved_flag(args: list[str], err_label: str) -> int | None:
    """Return an exit code if any deprecated flag is present, else None.

    We do this *first* so the redirect fires before we try to parse the
    remaining args as a prompt.
    """
    for flag, new in _MOVED_FLAGS.items():
        if flag in args:
            sys.stderr.write(
                f"{_RED}{err_label} error:{_RESET} `{flag}` moved — use `{new}` instead.\n"
            )
            return 2
    return None


def _print_history(session: SessionStore) -> None:
    """Dump the current session to stdout as plain text."""
    if session.is_empty():
        print("(no history)")
        return
    for m in session.messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if isinstance(content, str):
            print(f"--- {role} ---")
            print(content.rstrip())
            print()


def run_cli(
    argv: list[str],
    *,
    cmd: str,
    system: str,
    err_label: str,
) -> int:
    """Shared CLI dispatch for ``?`` (cmd='ask') and ``???`` (cmd='search')."""
    sweep_expired()
    memory = MemoryStore()
    archive = Archive()
    # On open we may rotate an expired session; ingest it into the
    # archive first so even transcripts the user never explicitly saves
    # become searchable later.
    session, expired = SessionStore.open(cmd=cmd, archive=archive, embed_fn=_safe_embed)
    claude_mem_override: bool | None = None

    # Pre-parse the small set of session/memory flags. We don't pull in
    # argparse — the surface is tiny and we want quoted multi-word prompts
    # to "just work" without an explicit `--` separator.
    args = list(argv)

    def _consume_flag(flag: str) -> bool:
        if flag in args:
            args.remove(flag)
            return True
        return False

    if _consume_flag("--help") or _consume_flag("-h"):
        _print_usage(err_label)
        return 0

    moved = _check_moved_flag(args, err_label)
    if moved is not None:
        return moved

    # --mem / --no-mem force the claude-mem integration on or off for
    # this invocation, overriding env vars.
    if _consume_flag("--no-mem"):
        claude_mem_override = False
    if _consume_flag("--mem"):
        claude_mem_override = True

    claude_mem = ClaudeMemAdapter(enabled_override=claude_mem_override)

    # --auto-recall (or SHELLLM_AUTO_RECALL=1) injects archive hits as
    # context on the first turn of a new session. --no-auto-recall
    # overrides the env var for this call.
    no_recall = _consume_flag("--no-auto-recall")
    auto_recall_flag = _consume_flag("--auto-recall")
    env_recall = os.environ.get("SHELLLM_AUTO_RECALL", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    auto_recall = (auto_recall_flag or env_recall) and not no_recall

    if _consume_flag("--reset"):
        session.archive_and_reset(archive=archive, embed_fn=_safe_embed)
        session.write()
        print(f"{cmd} session reset.")
        return 0

    if _consume_flag("--history"):
        _print_history(session)
        return 0

    if _consume_flag("--compact"):
        if session.is_empty():
            print("(no history to compact)")
            return 0
        new_messages, report = compact(list(session.messages), _summarize_via_model)
        session.messages = new_messages
        session.write()
        print(
            f"compacted {report.summarized_messages} messages: "
            f"{report.before_tokens}→{report.after_tokens} tokens "
            f"(triggered={report.triggered})"
        )
        return 0

    new_session_requested = _consume_flag("--new")
    if new_session_requested:
        session.archive_and_reset(archive=archive, embed_fn=_safe_embed)

    prompt = " ".join(args).strip()
    if not prompt:
        _print_usage(err_label, to=sys.stderr)
        return 2

    if expired:
        _note("idle session expired — starting fresh")

    try:
        return run_agent(
            prompt,
            system=system,
            session=session,
            resumed=not session.is_empty(),
            memory=memory,
            claude_mem=claude_mem,
            cmd_label=cmd,
            archive=archive,
            auto_recall=auto_recall,
        )
    except LlamaServerError as exc:
        sys.stderr.write(f"{_RED}{err_label} error:{_RESET} {exc}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write(f"\n{err_label} aborted\n")
        return 130


def main() -> int:
    return run_cli(sys.argv[1:], cmd="ask", system=ASK_SYSTEM, err_label="?")


if __name__ == "__main__":
    raise SystemExit(main())
