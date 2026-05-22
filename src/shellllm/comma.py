"""',' — propose shell commands, pipe through fzf, print the chosen one.

The whole point of the comma is that it *never executes*. This script
prints the chosen command on stdout; the zsh wrapper uses ``print -z``
to drop it on the next prompt line for the user to confirm.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

from .client import LlamaServerError, chat

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
_RESET = "\x1b[0m"

_err = Console(stderr=True)


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
        "alternative earlier in the list. Output must match the JSON schema."
    )


def _fzf_pick(items: list[dict[str, str]]) -> str | None:
    """Show items in fzf, colored, with the note inline on each row."""
    # Format: <colored line for display>\t<raw command for selection>
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
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    chosen = proc.stdout.strip()
    # Take the raw command from after the tab; fall back to whole line.
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


def main() -> int:
    prompt = " ".join(sys.argv[1:]).strip()
    if not prompt:
        sys.stderr.write("usage: , <what you want to do>\n")
        return 2

    messages = [
        {"role": "system", "content": _system_prompt()},
        {"role": "user", "content": f"{_context_block()}\n\n{prompt}"},
    ]

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
        _err.print(f"[red], error:[/red] {exc}")
        return 1

    content = reply.get("content") or "{}"
    try:
        parsed = json.loads(content)
        items = parsed.get("commands", [])
    except json.JSONDecodeError:
        _err.print(f"[red], error:[/red] model returned non-JSON: {content[:200]}")
        return 1

    if not items:
        _err.print("[red], error:[/red] no suggestions returned")
        return 1

    chosen = _pick(items)
    if not chosen:
        return 1
    print(chosen)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
