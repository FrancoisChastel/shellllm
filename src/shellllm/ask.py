"""'?' — answer a question with a narrow read-only agent. Streams markdown.

Three tools, all read-only:
  - read_file(path): goes through safe_fs (hard wall + denylist)
  - web_search(query): top 3 DuckDuckGo results, snippet text only
  - fetch_url(url): follow one of those links and read the page

Streamed answer renders as markdown via rich.live.Live, refreshing as
chunks arrive. Tool-call traces and errors go to stderr so a `… | less`
or `… > out.md` sees only the answer.

The agent loop is exported as ``run_agent`` so the ``???`` search command
(``shellllm.search``) can reuse it with a different system prompt.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown

from .client import LlamaServerError, chat_stream
from .context import build_prelude
from .safe_fs import WallViolation, safe_read_text
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


def run_agent(
    user_prompt: str,
    *,
    system: str = ASK_SYSTEM,
    include_context: bool = True,
) -> int:
    """Run the tool-calling agent loop. Returns a process-style exit code.

    Exposed so other entry points (e.g. ``shellllm-search``) can reuse the
    same dispatch + streaming machinery with their own system prompt.

    ``include_context=True`` prepends a date/time/OS prelude to the system
    message — useful for any tool-using mode, essential for web search
    where the model needs to know what "today" or "latest" means.
    """
    full_system = f"{build_prelude()}\n\n{system}" if include_context else system
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": full_system},
        {"role": "user", "content": user_prompt},
    ]

    # Live's redraw uses cursor positioning, which only makes sense on a
    # TTY. Piped/redirected stdout falls back to plain streaming.
    stream_round = _stream_round_markdown if sys.stdout.isatty() else _stream_round_plain

    for _ in range(MAX_ITERATIONS):
        full_text, tool_calls = stream_round(messages)

        if not tool_calls:
            return 0

        messages.append(
            {
                "role": "assistant",
                "content": full_text,
                "tool_calls": tool_calls,
            }
        )

        for call in tool_calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                args = {}
            _trace(name, json.dumps(args)[:120])
            result = _dispatch(name, args)
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": result[:MAX_TOOL_RESULT_CHARS],
                }
            )

    sys.stderr.write(f"{_RED}(stopped: hit tool-call iteration limit){_RESET}\n")
    return 1


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        sys.stderr.write("usage: ? <question>\n")
        return 2

    try:
        return run_agent(prompt)
    except LlamaServerError as exc:
        sys.stderr.write(f"{_RED}? error:{_RESET} {exc}\n")
        return 1
    except KeyboardInterrupt:
        sys.stderr.write("\n? aborted\n")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
