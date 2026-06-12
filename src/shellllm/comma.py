"""',' — propose shell commands, pipe through fzf, print the chosen one.

The whole point of the comma is that it *never executes*. This script
prints the chosen command on stdout; the zsh wrapper uses ``print -z``
to drop it on the next prompt line for the user to confirm.

Sticky session
~~~~~~~~~~~~~~
Each terminal pane has its own ``,`` thread (see :mod:`shellllm.session`).
The model sees prior user prompts and the JSON it previously emitted,
so follow-ups like ``, the same but only the running ones`` refine the
earlier proposal instead of asking from scratch. Sessions share the
same idle TTL and archive store as ``?`` / ``???``; expired ``,``
sessions are searchable via ``?: recall``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console

from .archive import Archive
from .client import LlamaServerError, chat
from .embed import embed as embed_text
from .session import SessionStore, sweep_expired
from .shell_context import build_shell_context_block

SCHEMA = {
    "type": "object",
    "properties": {
        "commands": {
            "type": "array",
            "minItems": 1,
            "maxItems": 5,
            "items": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "note": {"type": "string"},
                },
                "required": ["command", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["commands"],
    "additionalProperties": False,
}

# ANSI colors that fzf renders with `--ansi`. Kept as raw escapes so the
# picker stays dependency-free at the data layer.
_BOLD_CYAN = "\x1b[1;36m"
_DIM = "\x1b[2m"
_CYAN = "\x1b[36m"
_RED = "\x1b[31m"
_RESET = "\x1b[0m"

_err = Console(stderr=True)


def _note(text: str) -> None:
    """Dim-cyan one-liner straight to stderr.

    We bypass Rich here because ``rich.Console.print`` strips embedded
    ANSI escape characters as a safety measure (so untrusted strings
    can't redirect the cursor). That's the right default for rendered
    text, but our hint is fixed-content and we want the codes
    interpreted by the terminal — so we write to stderr directly.
    """
    sys.stderr.write(f"{_DIM}{_CYAN}↻ {text}{_RESET}\n")
    sys.stderr.flush()


def _safe_embed(text: str) -> list[float] | None:
    try:
        return embed_text(text)
    except Exception:  # noqa: BLE001
        return None


def _context_block() -> str:
    cwd = Path.cwd()
    try:
        listing = sorted(p.name for p in cwd.iterdir() if not p.name.startswith("."))[:20]
    except OSError:
        listing = []
    parts = [f"cwd: {cwd}", f"shell: {os.environ.get('SHELL', 'unknown')}"]
    if listing:
        parts.append(f"top-level files: {', '.join(listing)}")
    return "\n".join(parts)


def _system_prompt() -> str:
    return (
        "You are a shell-command suggester. The user describes what they want "
        "to do; you propose 3 to 5 shell commands that would do it. Each entry "
        "is a single one-line command (no `cd && ...` chains unless essential) "
        "and a terse note (≤80 chars) explaining what it does. Scope to the "
        "current directory unless the user clearly means system-wide. Favor "
        "commands that print rather than mutate. Never include `rm -rf`, "
        "`sudo`, `curl|sh`, or any destructive one-liner without a safer "
        "alternative earlier in the list. If the conversation includes prior "
        "suggestions and a refinement, build on the prior list rather than "
        "restarting from scratch. Output must match the JSON schema."
    )


def _print_usage(*, to: Any = None) -> None:
    out = to or sys.stdout
    out.write(
        "usage: , <what you want to do>          propose commands (no terminal context)\n"
        "       ,, <what you want to do>         same, with terminal context (, --ctx)\n"
        "       ,,                               fix the previous command (, --fix [hint])\n"
        "       , --new <what you want to do>   start a fresh session\n"
        "       , --reset                       drop current session\n"
        "       , --history                     print session transcript\n"
        "       , --fast|--balanced|--smart …   route this call to a tier (zsh)\n"
        "       , --help                        show this message\n"
        "\n"
        "For facts and cross-session recall, see `?: help`.\n"
    )


def _print_history(session: SessionStore) -> None:
    if session.is_empty():
        print("(no history)")
        return
    for m in session.messages:
        role = m.get("role", "?")
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        if role == "assistant":
            # The model stored a JSON blob; render its commands inline.
            try:
                parsed = json.loads(content)
                items = parsed.get("commands", [])
            except json.JSONDecodeError:
                items = []
            if items:
                print(f"--- {role} (suggestions) ---")
                for it in items:
                    print(f"  • {it.get('command', '')}  — {it.get('note', '')}")
                print()
                continue
        print(f"--- {role} ---")
        print(content.rstrip())
        print()


def _fzf_pick(items: list[dict[str, str]]) -> str | None:
    """Show items in fzf, colored, with the note inline on each row."""
    lines = []
    for it in items:
        cmd, note = it["command"], it["note"]
        colored = f"{_BOLD_CYAN}{cmd}{_RESET}  {_DIM}· {note}{_RESET}"
        lines.append(f"{colored}\t{cmd}")

    proc = subprocess.run(
        [
            "fzf",
            "--ansi",
            "--delimiter=\t",
            "--with-nth=1",
            "--height=40%",
            "--reverse",
            "--no-info",
            "--border=rounded",
            "--prompt=, ",
            "--pointer=▶",
            "--marker=▶",
            "--header=enter: drop on prompt · esc: cancel",
            "--header-first",
            "--color=hl:cyan,fg+:bright-white,hl+:bright-cyan,"
            "prompt:cyan,pointer:cyan,header:dim,border:dim",
        ],
        input="\n".join(lines),
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    chosen = proc.stdout.strip()
    return chosen.split("\t", 1)[1] if "\t" in chosen else chosen


def _stdin_pick(items: list[dict[str, str]]) -> str | None:
    """Numbered fallback when fzf isn't on $PATH."""
    _err.print()
    for i, item in enumerate(items, 1):
        _err.print(f"  [cyan]{i}.[/cyan] [bold]{item['command']}[/bold]")
        _err.print(f"     [dim]· {item['note']}[/dim]")
    _err.print()
    _err.print(
        f"[cyan]pick[/cyan] [dim][1-{len(items)}][/dim] (Enter to cancel): ",
        end="",
    )
    try:
        choice = input()
    except (EOFError, KeyboardInterrupt):
        return None
    if not choice.strip():
        return None
    try:
        idx = int(choice) - 1
    except ValueError:
        return None
    if 0 <= idx < len(items):
        return items[idx]["command"]
    return None


def _pick(items: list[dict[str, str]]) -> str | None:
    if not items:
        return None
    if shutil.which("fzf"):
        return _fzf_pick(items)
    return _stdin_pick(items)


def _build_messages(
    *,
    session: SessionStore,
    prompt: str,
    first_turn: bool,
    resumed: bool,
    shell_ctx: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (messages_to_send, history_to_persist_after).

    History is everything except the static system prompts we rebuild
    each turn — that way, ``$PWD`` changes take effect immediately and
    we don't bake a stale prelude into the on-disk log.
    """
    pwd = str(Path.cwd())
    date = datetime.now().astimezone().strftime("%Y-%m-%d")

    system_msgs: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt()},
    ]
    if first_turn or resumed or session.meta.last_pwd != pwd or session.meta.last_date != date:
        system_msgs.append({"role": "system", "content": _context_block()})

    # Terminal context is opt-in per call (`--ctx` / `--fix`) and
    # per-turn ephemeral: rebuilt every call, never persisted (system
    # messages are stripped before the session is written).
    if shell_ctx:
        ctx_block = build_shell_context_block()
        if ctx_block:
            system_msgs.append({"role": "system", "content": ctx_block})

    history = list(session.messages)
    user_msg: dict[str, Any] = {"role": "user", "content": prompt}
    return system_msgs + history + [user_msg], history + [user_msg]


def _ask_model(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, str]]] | None:
    try:
        with _err.status("[cyan]thinking…[/cyan]", spinner="dots"):
            reply = chat(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "commands", "schema": SCHEMA, "strict": True},
                },
                max_tokens=512,
            )
    except LlamaServerError as exc:
        _err.print(f"{_RED}, error:{_RESET} {exc}")
        return None

    content = reply.get("content") or "{}"
    try:
        parsed = json.loads(content)
        items = parsed.get("commands", [])
    except json.JSONDecodeError:
        _err.print(f"{_RED}, error:{_RESET} model returned non-JSON: {content[:200]}")
        return None
    if not items:
        _err.print(f"{_RED}, error:{_RESET} no suggestions returned")
        return None
    return content, items


def main() -> int:
    sweep_expired()

    argv = list(sys.argv[1:])

    def _consume_flag(flag: str) -> bool:
        if flag in argv:
            argv.remove(flag)
            return True
        return False

    if _consume_flag("--help") or _consume_flag("-h"):
        _print_usage()
        return 0

    archive = Archive()
    session, expired = SessionStore.open(cmd="comma", archive=archive, embed_fn=_safe_embed)

    if _consume_flag("--reset"):
        session.archive_and_reset(archive=archive, embed_fn=_safe_embed)
        session.write()
        print(", session reset.")
        return 0

    if _consume_flag("--history"):
        _print_history(session)
        return 0

    if _consume_flag("--new"):
        session.archive_and_reset(archive=archive, embed_fn=_safe_embed)

    # `,` is the context-free verb; `--ctx` (the zsh `,, <prompt>`) brings
    # the terminal context along, and `--fix` (bare `,,`) implies it.
    ctx_mode = _consume_flag("--ctx")
    fix_mode = _consume_flag("--fix")
    shell_ctx = ctx_mode or fix_mode

    prompt = " ".join(argv).strip()

    if ctx_mode and not fix_mode and not build_shell_context_block():
        _note("terminal context unavailable (SHELLLM_SHELL_CONTEXT=off?) — proceeding without")

    if fix_mode:
        if not build_shell_context_block():
            _err.print(f"{_RED}, error:{_RESET} --fix needs terminal context, and none arrived.")
            _err.print(
                "  if you set SHELLLM_SHELL_CONTEXT=off, re-enable it: export SHELLLM_SHELL_CONTEXT=cmd"
            )
            _err.print("  otherwise re-source zsh/shellllm.zsh (older versions didn't capture).")
            return 2
        repair = (
            "Diagnose the previous command using the terminal context "
            "(command, exit status, output if present) and propose corrected "
            "commands that do what the user wanted."
        )
        prompt = f"{repair} Hint from the user: {prompt}" if prompt else repair

    if not prompt:
        _print_usage(to=sys.stderr)
        return 2

    if expired:
        _note("idle session expired — starting fresh")

    first_turn = session.is_empty()
    resumed = not first_turn
    if resumed:
        _note(f"refining — turn {session.meta.turn_count + 1}")

    messages, new_history_with_user = _build_messages(
        session=session,
        prompt=prompt,
        first_turn=first_turn,
        resumed=resumed,
        shell_ctx=shell_ctx,
    )

    result = _ask_model(messages)
    if result is None:
        return 1
    content, items = result

    chosen = _pick(items)
    if not chosen:
        return 1

    # Persist the turn for the next refinement. We store the raw JSON
    # the model produced so it sees its own prior list verbatim.
    new_history_with_user.append({"role": "assistant", "content": content})
    session.messages = new_history_with_user
    pwd = str(Path.cwd())
    date = datetime.now().astimezone().strftime("%Y-%m-%d")
    session.touch(pwd=pwd, date=date)
    session.write()

    print(chosen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
