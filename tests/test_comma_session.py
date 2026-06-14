"""Tests for `,` (comma) per-pane sessions.

The LLM call itself is mocked so we exercise only the session/flag
plumbing — the JSON shape from the model is fixed and the picker is
short-circuited.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import pytest

from shellllm import comma
from shellllm.session import SessionStore


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect every persistent path so tests can't poison the real cache."""
    monkeypatch.setenv("SHELLLM_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("SHELLLM_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("TERM_SESSION_ID", "test-pane-1")
    return tmp_path


@pytest.fixture
def fake_model(monkeypatch):
    """Patch chat() to return a fixed JSON payload. Captures messages sent.

    Adapts to the requested schema: fix mode (``json_schema.name == "fix"``)
    gets an extra ``diagnosis`` field, normal mode gets just ``commands``.
    """
    sent: list[list[dict]] = []

    def fake_chat(messages, **kwargs):
        sent.append(list(messages))
        name = ((kwargs.get("response_format") or {}).get("json_schema") or {}).get("name", "")
        payload: dict[str, Any] = {
            "commands": [
                {"command": "ls -lh", "note": "list with sizes"},
                {"command": "ls -la", "note": "include dotfiles"},
            ]
        }
        if name == "fix":
            payload["diagnosis"] = "Typo: the test fake diagnosed the failure."
        return {"content": json.dumps(payload)}

    monkeypatch.setattr(comma, "chat", fake_chat)
    return sent


@pytest.fixture
def auto_pick(monkeypatch):
    """Skip the picker — always return the first command."""

    def first_pick(items):
        return items[0]["command"]

    monkeypatch.setattr(comma, "_pick", first_pick)


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["shellllm-comma", *argv])
    return comma.main()


def test_help_returns_zero(monkeypatch, capsys, isolated):
    assert _run(["--help"], monkeypatch) == 0
    assert "usage: ," in capsys.readouterr().out


def test_no_args_prints_usage(monkeypatch, capsys, isolated):
    assert _run([], monkeypatch) == 2
    assert "usage: ," in capsys.readouterr().err


def test_first_invocation_persists_session(monkeypatch, capsys, isolated, fake_model, auto_pick):
    code = _run(["list", "files", "in", "this", "dir"], monkeypatch)
    out = capsys.readouterr().out
    assert code == 0
    assert "ls -lh" in out
    # Session was written.
    store, _ = SessionStore.open(cmd="comma")
    assert len(store.messages) == 2  # user + assistant
    assert store.messages[0]["role"] == "user"
    assert store.messages[1]["role"] == "assistant"


def test_second_invocation_carries_prior_turn(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _run(["list", "files"], monkeypatch)
    capsys.readouterr()
    fake_model.clear()
    _run(["the", "same", "but", "with", "hidden", "files"], monkeypatch)
    # The model received the prior user + assistant in its messages.
    sent = fake_model[0]
    roles = [m["role"] for m in sent]
    assert roles.count("user") == 2  # the first refinement turn + this one
    assert roles.count("assistant") == 1


def test_reset_drops_session(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _run(["list", "files"], monkeypatch)
    capsys.readouterr()
    assert _run(["--reset"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "session reset" in out
    store, _ = SessionStore.open(cmd="comma")
    assert store.is_empty()


def test_history_dumps_prior_suggestions(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _run(["list", "files"], monkeypatch)
    capsys.readouterr()
    assert _run(["--history"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "ls -lh" in out
    assert "list with sizes" in out


def test_history_when_empty(monkeypatch, capsys, isolated):
    assert _run(["--history"], monkeypatch) == 0
    assert "(no history)" in capsys.readouterr().out


def test_new_flag_archives_and_starts_fresh(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _run(["list", "files"], monkeypatch)
    capsys.readouterr()
    fake_model.clear()
    _run(["--new", "find", "biggest", "files"], monkeypatch)
    # The model received no prior user/assistant — fresh session.
    sent = fake_model[0]
    roles = [m["role"] for m in sent]
    assert roles.count("user") == 1
    assert roles.count("assistant") == 0


def test_session_uses_comma_cmd_key(monkeypatch, isolated, fake_model, auto_pick):
    """Comma sessions live under cmd='comma', distinct from `?` (ask)."""

    _run(["x"], monkeypatch)
    comma_store, _ = SessionStore.open(cmd="comma")
    ask_store, _ = SessionStore.open(cmd="ask")
    assert comma_store.path != ask_store.path
    assert len(comma_store.messages) > 0
    assert ask_store.is_empty()


def test_expired_session_archives_to_recall(
    monkeypatch, capsys, isolated, fake_model, auto_pick, tmp_path
):
    """When the comma session expires, its transcript reaches the archive
    so `?: recall` can find it later."""

    from shellllm.archive import Archive

    _run(["search", "for", "ripgrep", "binaries"], monkeypatch)
    capsys.readouterr()

    # Bump time past TTL so the next open rotates and archives.
    store, _ = SessionStore.open(
        cmd="comma",
        archive=Archive(),
        now=9_999_999_999.0,
    )
    assert store.is_empty()
    hits = Archive().search("ripgrep")
    assert hits
    assert any(h.cmd == "comma" for h in hits)


def test_resume_hint_writes_raw_ansi_not_rich_markup(
    monkeypatch, capsys, isolated, fake_model, auto_pick
):
    """The "↻ refining" hint must reach the terminal as a real ANSI
    sequence (starting with ESC), not the literal ``[2m`` text Rich's
    Console.print would render after stripping the ESC byte."""

    _run(["first"], monkeypatch)
    capsys.readouterr()
    _run(["second"], monkeypatch)
    err = capsys.readouterr().err
    # ESC byte must be present; literal "[2m[36m" never appears alone.
    assert "\x1b[2m" in err or "\x1b[36m" in err
    assert "↻ refining" in err


def _enable_shell_ctx(monkeypatch, *, status="1"):
    monkeypatch.setenv("SHELLLM_SHELL_CONTEXT", "cmd")
    monkeypatch.setenv("SHELLLM_LAST_CMD", "git push origin main")
    monkeypatch.setenv("SHELLLM_LAST_STATUS", status)


def test_ctx_flag_injects_shell_context(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _enable_shell_ctx(monkeypatch)
    _run(["--ctx", "retry", "that"], monkeypatch)
    sent = fake_model[0]
    system_text = "\n".join(m["content"] for m in sent if m["role"] == "system")
    assert "git push origin main" in system_text


def test_plain_comma_never_injects_shell_context(
    monkeypatch, capsys, isolated, fake_model, auto_pick
):
    """`,` is the context-free verb — even with the ladder enabled."""
    _enable_shell_ctx(monkeypatch)
    _run(["list", "files"], monkeypatch)
    sent = fake_model[0]
    system_text = "\n".join(m["content"] for m in sent if m["role"] == "system")
    assert "git push origin main" not in system_text


def test_ctx_flag_with_ladder_off_hints(monkeypatch, capsys, isolated, fake_model, auto_pick):
    monkeypatch.delenv("SHELLLM_SHELL_CONTEXT", raising=False)
    monkeypatch.setenv("SHELLLM_LAST_CMD", "git push origin main")
    assert _run(["--ctx", "list", "files"], monkeypatch) == 0
    sent = fake_model[0]
    system_text = "\n".join(m["content"] for m in sent if m["role"] == "system")
    assert "git push origin main" not in system_text
    assert "terminal context unavailable" in capsys.readouterr().err


def test_shell_context_never_persisted(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _enable_shell_ctx(monkeypatch)
    _run(["--ctx", "retry", "that"], monkeypatch)
    store, _ = SessionStore.open(cmd="comma")
    assert all(m.get("role") != "system" for m in store.messages)


def test_fix_without_context_errors_with_hint(monkeypatch, capsys, isolated):
    monkeypatch.delenv("SHELLLM_SHELL_CONTEXT", raising=False)
    assert _run(["--fix"], monkeypatch) == 2
    err = capsys.readouterr().err
    assert "SHELLLM_SHELL_CONTEXT" in err


def test_fix_builds_repair_prompt(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _enable_shell_ctx(monkeypatch)
    assert _run(["--fix"], monkeypatch) == 0
    sent = fake_model[0]
    user_msg = next(m["content"] for m in sent if m["role"] == "user")
    # User message stays minimal — the contract lives in the system prompt.
    assert "fix" in user_msg.lower()
    system_text = "\n".join(m["content"] for m in sent if m["role"] == "system")
    # Fix mode swaps the system prompt to the repair-specific one,
    # teaching the model to diagnose and categorise the failure.
    assert "DIAGNOSE" in system_text
    assert "Typo:" in system_text
    assert "Environment:" in system_text
    # Terminal context still rides along.
    assert "git push origin main" in system_text


def test_fix_appends_user_hint(monkeypatch, capsys, isolated, fake_model, auto_pick):
    _enable_shell_ctx(monkeypatch)
    assert _run(["--fix", "I", "meant", "the", "dev", "branch"], monkeypatch) == 0
    user_msg = next(m["content"] for m in fake_model[0] if m["role"] == "user")
    assert "I meant the dev branch" in user_msg


def test_fix_uses_fix_schema(monkeypatch, capsys, isolated, fake_model, auto_pick):
    """Fix mode must request the diagnose-then-suggest schema, not the plain one."""
    _enable_shell_ctx(monkeypatch)
    sent_kwargs: list = []

    def fake_chat_capture(messages, **kwargs):
        sent_kwargs.append(kwargs)
        return {
            "content": json.dumps(
                {
                    "diagnosis": "Typo: --grpe should be --grep.",
                    "commands": [{"command": "git log --grep fix", "note": "fix"}],
                }
            )
        }

    monkeypatch.setattr(comma, "chat", fake_chat_capture)
    assert _run(["--fix"], monkeypatch) == 0
    schema_name = sent_kwargs[0]["response_format"]["json_schema"]["name"]
    assert schema_name == "fix"


def test_fix_surfaces_diagnosis_on_stderr(monkeypatch, capsys, isolated, fake_model, auto_pick):
    """The 1-sentence diagnosis must reach the user before the picker."""
    _enable_shell_ctx(monkeypatch)
    assert _run(["--fix"], monkeypatch) == 0
    err = capsys.readouterr().err
    # The fake_model fixture stamps a deterministic diagnosis when fix mode is used.
    assert "Typo: the test fake diagnosed the failure." in err


def test_plain_comma_does_not_print_diagnosis(monkeypatch, capsys, isolated, fake_model, auto_pick):
    """Diagnosis is fix-mode only; non-fix turns must stay quiet."""
    _run(["list", "files"], monkeypatch)
    err = capsys.readouterr().err
    assert "diagnosed the failure" not in err


def test_fix_starts_fresh_session(monkeypatch, capsys, isolated, fake_model, auto_pick):
    """`,,` (--fix) is a one-shot repair, not a refinement of the prior `,` thread.

    Without this, the model would propose variants of the last question
    instead of the actual fix for the failed command.
    """
    _enable_shell_ctx(monkeypatch)
    # Establish a sticky session from a prior plain `,` turn.
    _run(["list", "files"], monkeypatch)
    capsys.readouterr()
    fake_model.clear()

    _run(["--fix"], monkeypatch)
    sent = fake_model[0]
    roles = [m["role"] for m in sent]
    # Only the new user turn should reach the model — no prior
    # user/assistant from the `list files` turn.
    assert roles.count("user") == 1
    assert roles.count("assistant") == 0


def test_redirect_for_ask_remember(monkeypatch, capsys, isolated):
    """`,` doesn't share `?`'s deprecation hints — it has its own surface."""

    # No such flag in comma; it lands in the prompt and gets sent to model.
    # We only verify it doesn't error mysteriously.
    from shellllm import comma as comma_mod

    captured: list = []
    monkeypatch.setattr(
        comma_mod,
        "chat",
        lambda messages, **kw: (
            captured.append(messages),
            {"content": json.dumps({"commands": [{"command": "echo ok", "note": ""}]})},
        )[1],
    )
    monkeypatch.setattr(comma_mod, "_pick", lambda items: items[0]["command"])
    assert _run(["--remember", "ripgrep"], monkeypatch) == 0
    # The flag became part of the prompt; the model still answered.
    user_msg = next(m["content"] for m in captured[0] if m["role"] == "user")
    assert "--remember" in user_msg
