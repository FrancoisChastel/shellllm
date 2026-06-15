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
sessions are searchable via ``??? <query>``.
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

_COMMANDS_PROPERTY = {
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

SCHEMA = {
    "type": "object",
    "properties": {"commands": _COMMANDS_PROPERTY},
    "required": ["commands"],
    "additionalProperties": False,
}

# Fix mode adds a single-sentence diagnosis that prints to stderr above
# the picker. It tells the user WHY their command failed (typo, env,
# logic) so a "git init"-style suggestion doesn't look like the model
# missing the question — it's the model correctly diagnosing that the
# environment, not the syntax, is the problem.
FIX_SCHEMA = {
    "type": "object",
    "properties": {
        "diagnosis": {
            "type": "string",
            "description": (
                "One short sentence on WHY the previous command failed. "
                "Lead with the category: 'Typo:', 'Environment:', or 'Logic:'."
            ),
        },
        "commands": _COMMANDS_PROPERTY,
    },
    "required": ["diagnosis", "commands"],
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


def _fix_system_prompt() -> str:
    """Used by `,,` / `, --fix`. Replaces the regular system prompt."""
    return (
        "You are a shell-command REPAIR assistant. The terminal context shows "
        "a command that just failed (with its exit status and any output). "
        "Do TWO things, in this order:\n"
        "\n"
        "1. DIAGNOSE in one short sentence WHY the command failed. Pick one "
        "of three categories and lead with it:\n"
        "   - 'Typo:' the syntax is broken (wrong flag, transposed letters, "
        "missing arg). Example: 'Typo: `--grpe` should be `--grep`.'\n"
        "   - 'Environment:' the command is fine but the world isn't (no "
        "such file, not a git repo, permission denied, missing tool). "
        "Example: 'Environment: not inside a git repository.'\n"
        "   - 'Logic:' the command ran but did the wrong thing for the "
        "user's goal. Example: 'Logic: `-type d` excludes regular files; "
        "the *.log files are files, not directories.'\n"
        "\n"
        "2. Propose 3 to 5 ACTIONABLE next commands. The FIRST item is your "
        "best single-shot guess at what the user actually wants to do now. "
        "Match the category:\n"
        "   - Typo → the corrected command.\n"
        "   - Environment → the setup that unblocks it (`git init`, "
        "`mkdir -p ...`, `cd ../other-dir`, `chmod +r ...`), then the "
        "original-as-intended command for after that.\n"
        "   - Logic → a different command that achieves the actual goal.\n"
        "\n"
        "Each entry is one shell line + a terse note (≤80 chars). NEVER "
        "include `rm -rf`, `sudo`, or other destructive commands. NEVER "
        "propose commands that only inspect the failure (`echo $?`, "
        "`history`, `man`, `pwd`). Output must match the JSON schema."
    )


def _print_usage(*, to: Any = None) -> None:
    out = to or sys.stdout
    out.write(
        "usage: , <prompt>                       propose commands via fzf picker\n"
        "       ,,                               fix the previous command (top fix → prompt line)\n"
        "       ,, <intent>                      fix using your stated intent\n"
        "       ,, --pick [intent]               same, but show the picker (see alternatives)\n"
        "       , --ctx <prompt>                propose with terminal context as background\n"
        "       , --new <prompt>                start a fresh session\n"
        "       , --reset                       drop current session\n"
        "       , --history                     print session transcript\n"
        "       , --fast|--balanced|--smart …   route this call to a tier (zsh)\n"
        "       , --help                        show this message\n"
        "\n"
        "`,,` never executes anything — the suggestion lands on your prompt;\n"
        "you confirm with Enter, edit it, or cancel with Ctrl-C.\n"
        "\n"
        "For facts and cross-session recall, see `??? --help`.\n"
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
    fix_mode: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (messages_to_send, history_to_persist_after).

    History is everything except the static system prompts we rebuild
    each turn — that way, ``$PWD`` changes take effect immediately and
    we don't bake a stale prelude into the on-disk log.
    """
    pwd = str(Path.cwd())
    date = datetime.now().astimezone().strftime("%Y-%m-%d")

    system_msgs: list[dict[str, Any]] = [
        {"role": "system", "content": _fix_system_prompt() if fix_mode else _system_prompt()},
    ]
    # The cwd-listing context is helpful for "what should I do next"
    # questions but actively distracting in repair mode — the model
    # tends to anchor on filenames it sees in `cwd: files…` and propose
    # commands to operate on them, ignoring the actual failed command.
    if not fix_mode and (
        first_turn or resumed or session.meta.last_pwd != pwd or session.meta.last_date != date
    ):
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


def _ask_model(
    messages: list[dict[str, Any]],
    *,
    fix_mode: bool = False,
) -> tuple[str, list[dict[str, str]]] | None:
    schema = FIX_SCHEMA if fix_mode else SCHEMA
    name = "fix" if fix_mode else "commands"
    try:
        with _err.status("[cyan]thinking…[/cyan]", spinner="dots"):
            reply = chat(
                messages,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": True},
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

    # In fix mode the model also produced a one-line diagnosis. Surface
    # it on stderr so the user reads "why it failed" before the picker
    # opens — turns a confusing "model proposed git init" into "Ah, not
    # in a git repo, here's how to unblock."
    diagnosis = (parsed.get("diagnosis") or "").strip() if fix_mode else ""
    if diagnosis:
        sys.stderr.write(f"{_DIM}{_CYAN}↻ {diagnosis}{_RESET}\n")
        sys.stderr.flush()

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

    # `,` is the context-free verb; `, --ctx <prompt>` brings the
    # terminal context along (the zsh `,,` glyph routes here when run
    # with a prompt that the user wants treated as a refinement, not a
    # repair); `, --fix` (the zsh bare `,,`) repairs the previous
    # command. Fix-mode drops the model's top suggestion straight on
    # the prompt — pass `--pick` to see the full picker instead.
    ctx_mode = _consume_flag("--ctx")
    fix_mode = _consume_flag("--fix")
    pick_mode = _consume_flag("--pick")
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
        # A repair is a one-shot diagnosis of the SHELL state, not a
        # refinement of the prior `,` thread — continuing the session
        # would have the model proposing variants of the last question
        # ("find largest files") instead of the actual fix. Rotate it
        # into the archive (still searchable via `???`) and start fresh.
        if not session.is_empty():
            session.archive_and_reset(archive=archive, embed_fn=_safe_embed)
        # The system prompt (_fix_system_prompt) already binds the model
        # to the repair contract — keep the user message minimal so the
        # terminal context dominates.
        prompt = (
            f"Fix the previous command. Hint: {prompt}" if prompt else "Fix the previous command."
        )

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
        fix_mode=fix_mode,
    )

    result = _ask_model(messages, fix_mode=fix_mode)
    if result is None:
        return 1
    content, items = result

    # In fix mode (the typical `,,` flow) we trust the model's top
    # suggestion and drop it straight on the user's prompt line — the
    # diagnose-then-suggest design already surfaces *why* the model
    # thinks this is the fix above. The picker is one keystroke too
    # many for obvious typos. `,, --pick` (or `, --fix --pick`) opts
    # back into the picker when alternatives matter.
    if fix_mode and not pick_mode:
        chosen = items[0]["command"]
    else:
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
