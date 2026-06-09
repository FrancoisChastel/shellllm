"""Tests for `,` (comma) per-pane sessions.

The LLM call itself is mocked so we exercise only the session/flag
plumbing — the JSON shape from the model is fixed and the picker is
short-circuited.
"""

from __future__ import annotations

import json
import sys

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
    """Patch chat() to return a fixed JSON payload. Captures messages sent."""
    sent: list[list[dict]] = []

    def fake_chat(messages, **kwargs):
        sent.append(list(messages))
        return {
            "content": json.dumps(
                {
                    "commands": [
                        {"command": "ls -lh", "note": "list with sizes"},
                        {"command": "ls -la", "note": "include dotfiles"},
                    ]
                }
            )
        }

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
