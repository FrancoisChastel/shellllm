"""Tests for the `?:` (shellllm-state) CLI."""

from __future__ import annotations

import sys

import pytest

from shellllm import state


@pytest.fixture
def isolated_state(tmp_path, monkeypatch):
    """Redirect memory + archive paths so the CLI doesn't touch the real ones."""
    monkeypatch.setenv("SHELLLM_MEMORY_FILE", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("SHELLLM_ARCHIVE_DB", str(tmp_path / "archive.db"))
    return tmp_path


def _run(argv: list[str], monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["shellllm-state", *argv])
    return state.main()


def test_bare_invocation_prints_usage(monkeypatch, capsys, isolated_state):
    code = _run([], monkeypatch)
    out = capsys.readouterr().out
    assert code == 0
    assert "usage: ?:" in out


def test_help_subcommand(monkeypatch, capsys, isolated_state):
    assert _run(["help"], monkeypatch) == 0
    assert "usage: ?:" in capsys.readouterr().out


def test_add_then_list_round_trips(monkeypatch, capsys, isolated_state):
    assert _run(["add", "the", "project", "uses", "python"], monkeypatch) == 0
    capsys.readouterr()
    assert _run(["list"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "the project uses python" in out


def test_add_with_no_args_errors(monkeypatch, capsys, isolated_state):
    code = _run(["add"], monkeypatch)
    captured = capsys.readouterr()
    assert code == 2
    assert "needs a fact" in captured.err


def test_drop_removes_by_index(monkeypatch, capsys, isolated_state):
    _run(["add", "alpha"], monkeypatch)
    _run(["add", "beta"], monkeypatch)
    capsys.readouterr()
    assert _run(["drop", "1"], monkeypatch) == 0
    capsys.readouterr()
    assert _run(["list"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "alpha" not in out
    assert "beta" in out


def test_drop_without_index_errors(monkeypatch, capsys, isolated_state):
    assert _run(["drop"], monkeypatch) == 2
    assert "needs an index" in capsys.readouterr().err


def test_drop_with_non_integer_errors(monkeypatch, capsys, isolated_state):
    assert _run(["drop", "abc"], monkeypatch) == 2
    assert "integer" in capsys.readouterr().err


def test_drop_out_of_range_errors(monkeypatch, capsys, isolated_state):
    _run(["add", "x"], monkeypatch)
    capsys.readouterr()
    assert _run(["drop", "99"], monkeypatch) == 2
    assert "no fact at index" in capsys.readouterr().err


def test_status_reports_counts(monkeypatch, capsys, isolated_state):
    _run(["add", "a"], monkeypatch)
    _run(["add", "b"], monkeypatch)
    capsys.readouterr()
    assert _run(["status"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "2 remembered facts" in out
    assert "0 archived sessions" in out


def test_recall_without_query_errors(monkeypatch, capsys, isolated_state):
    assert _run(["recall"], monkeypatch) == 2
    assert "needs a query" in capsys.readouterr().err


def test_recall_empty_archive_returns_quietly(monkeypatch, capsys, isolated_state):
    assert _run(["recall", "ripgrep"], monkeypatch) == 0
    assert "no archive hits" in capsys.readouterr().out


def test_recall_finds_archived_session(monkeypatch, capsys, isolated_state):
    """Seed the archive directly and verify recall surfaces it."""

    from shellllm.archive import Archive

    Archive().ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="/tmp",
        last_date="2026-06-08",
        turn_count=1,
        messages=[
            {"role": "user", "content": "how do I use ripgrep"},
            {"role": "assistant", "content": "rg pattern path"},
        ],
    )
    assert _run(["recall", "ripgrep"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "ripgrep" in out.lower()


def test_unknown_subcommand_errors(monkeypatch, capsys, isolated_state):
    assert _run(["explode"], monkeypatch) == 2
    err = capsys.readouterr().err
    assert "unknown subcommand" in err
