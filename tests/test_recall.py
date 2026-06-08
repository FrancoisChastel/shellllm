"""Tests for the `???` (shellllm-recall) CLI.

Every operation other than bare-query recall is a flag — no
subcommand verbs. Tests cover mode-flag dispatch, filter flags,
multi-flag rejection, and the bare-query fallback.
"""

from __future__ import annotations

import sys

import pytest

from shellllm import recall


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Redirect persistent paths so the CLI can't touch the real cache."""
    monkeypatch.setenv("SHELLLM_MEMORY_FILE", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("SHELLLM_ARCHIVE_DB", str(tmp_path / "archive.db"))
    return tmp_path


def _run(argv: list[str], monkeypatch) -> int:
    monkeypatch.setattr(sys, "argv", ["shellllm-recall", *argv])
    return recall.main()


# ── Dispatch -------------------------------------------------------------


def test_no_args_prints_usage(monkeypatch, capsys, isolated):
    assert _run([], monkeypatch) == 0
    assert "usage: ???" in capsys.readouterr().out


def test_help_flag(monkeypatch, capsys, isolated):
    for variant in (["--help"], ["-h"]):
        assert _run(variant, monkeypatch) == 0
        assert "usage: ???" in capsys.readouterr().out


def test_bare_query_routes_to_recall(monkeypatch, capsys, isolated):
    assert _run(["what", "was", "that", "grep", "flag"], monkeypatch) == 0
    assert "no archive hits" in capsys.readouterr().out


def test_bare_query_starting_with_word_list_still_recalls(monkeypatch, capsys, isolated):
    """No bare-word subcommands → `list` is a regular search term now."""
    assert _run(["list"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "no archive hits for 'list'" in out


# ── Facts (mode flags) ---------------------------------------------------


def test_add_then_list(monkeypatch, capsys, isolated):
    assert _run(["--add", "the", "project", "uses", "python"], monkeypatch) == 0
    capsys.readouterr()
    assert _run(["--list"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "the project uses python" in out


def test_add_empty_errors(monkeypatch, capsys, isolated):
    assert _run(["--add"], monkeypatch) == 2
    assert "needs a fact" in capsys.readouterr().err


def test_list_with_extra_args_errors(monkeypatch, capsys, isolated):
    assert _run(["--list", "extra"], monkeypatch) == 2
    assert "takes no arguments" in capsys.readouterr().err


def test_drop_removes_by_index(monkeypatch, capsys, isolated):
    _run(["--add", "alpha"], monkeypatch)
    _run(["--add", "beta"], monkeypatch)
    capsys.readouterr()
    assert _run(["--drop", "1"], monkeypatch) == 0
    capsys.readouterr()
    assert _run(["--list"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "alpha" not in out
    assert "beta" in out


def test_drop_non_integer_errors(monkeypatch, capsys, isolated):
    assert _run(["--drop", "abc"], monkeypatch) == 2
    assert "integer" in capsys.readouterr().err


def test_drop_out_of_range_errors(monkeypatch, capsys, isolated):
    _run(["--add", "x"], monkeypatch)
    capsys.readouterr()
    assert _run(["--drop", "99"], monkeypatch) == 2
    assert "no fact at index" in capsys.readouterr().err


def test_drop_no_arg_errors(monkeypatch, capsys, isolated):
    assert _run(["--drop"], monkeypatch) == 2
    assert "needs an index" in capsys.readouterr().err


def test_drop_multiple_args_errors(monkeypatch, capsys, isolated):
    assert _run(["--drop", "1", "2"], monkeypatch) == 2
    assert "one index" in capsys.readouterr().err


def test_status_reports_counts(monkeypatch, capsys, isolated):
    _run(["--add", "a"], monkeypatch)
    _run(["--add", "b"], monkeypatch)
    capsys.readouterr()
    assert _run(["--status"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "2 remembered facts" in out
    assert "0 archived sessions" in out


def test_status_with_args_errors(monkeypatch, capsys, isolated):
    assert _run(["--status", "extra"], monkeypatch) == 2
    assert "takes no arguments" in capsys.readouterr().err


# ── Multi-flag rejection -------------------------------------------------


def test_two_mode_flags_errors(monkeypatch, capsys, isolated):
    assert _run(["--add", "x", "--list"], monkeypatch) == 2
    assert "only one of" in capsys.readouterr().err


def test_two_filter_flags_errors(monkeypatch, capsys, isolated):
    assert _run(["--ask", "--comma", "x"], monkeypatch) == 2
    assert "only one of" in capsys.readouterr().err


def test_filter_with_mode_flag_errors(monkeypatch, capsys, isolated):
    """Filters only apply to recall, not to fact management."""
    assert _run(["--ask", "--list"], monkeypatch) == 2
    err = capsys.readouterr().err
    assert "only apply to recall" in err


# ── Filter flags ---------------------------------------------------------


def _seed_two_cmd_archives() -> None:
    """Seed one `ask` and one `comma` archive row sharing a search term."""
    from shellllm.archive import Archive

    a = Archive()
    a.ingest_session(
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
    a.ingest_session(
        cmd="comma",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="/tmp",
        last_date="2026-06-08",
        turn_count=1,
        messages=[
            {"role": "user", "content": "ripgrep search incantation"},
            {"role": "assistant", "content": '{"commands":[{"command":"rg foo","note":""}]}'},
        ],
    )


def test_ask_filter_returns_only_ask_rows(monkeypatch, capsys, isolated):
    _seed_two_cmd_archives()
    assert _run(["--ask", "ripgrep"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert out.count("ask") >= 1
    assert "comma" not in out


def test_comma_filter_returns_only_comma_rows(monkeypatch, capsys, isolated):
    _seed_two_cmd_archives()
    assert _run(["--comma", "ripgrep"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "comma" in out
    # The ask row's snippet body should NOT appear.
    assert "rg pattern" not in out


def test_filter_no_hits_includes_scope_in_message(monkeypatch, capsys, isolated):
    assert _run(["--ask", "totallyabsent"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "in `ask` sessions" in out


def test_filter_with_no_query_errors(monkeypatch, capsys, isolated):
    assert _run(["--ask"], monkeypatch) == 2
    assert "no query" in capsys.readouterr().err


# ── Bare-recall edge cases -----------------------------------------------


def test_bare_recall_finds_archived_session(monkeypatch, capsys, isolated):
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
    assert _run(["ripgrep"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "ripgrep" in out.lower()


def test_bare_multiword_query_is_joined(monkeypatch, capsys, isolated):
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
            {"role": "user", "content": "how do docker volumes work"},
            {"role": "assistant", "content": "they mount paths into the container"},
        ],
    )
    assert _run(["docker", "volumes"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "docker" in out.lower()
