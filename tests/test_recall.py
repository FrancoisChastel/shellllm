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
    """Filters apply only to recall and --archives, not to fact management."""
    assert _run(["--ask", "--list"], monkeypatch) == 2
    err = capsys.readouterr().err
    assert "only apply to recall" in err


def test_filter_with_archives_is_allowed(monkeypatch, capsys, isolated):
    """--archives is the one mode flag that accepts filters."""
    assert _run(["--ask", "--archives"], monkeypatch) == 0
    out = capsys.readouterr().out
    # No data → scoped empty-state message confirms the filter took effect.
    assert "in `ask` sessions" in out


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


def _seed_archive_rows(n: int = 3, cmd: str = "ask") -> None:
    """Insert N distinct archive rows for browse-mode tests."""
    from shellllm.archive import Archive

    a = Archive()
    for i in range(n):
        a.ingest_session(
            cmd=cmd,
            terminal_id="t1",
            created_at=1.0 + i,
            last_used=2.0 + i,
            last_pwd=f"/tmp/p{i}",
            last_date="2026-06-08",
            turn_count=1,
            messages=[
                {"role": "user", "content": f"question number {i}"},
                {"role": "assistant", "content": f"answer body {i}"},
            ],
            archived_at=1000.0 + i,
        )


# ── --archives ----------------------------------------------------------


def test_archives_empty(monkeypatch, capsys, isolated):
    assert _run(["--archives"], monkeypatch) == 0
    assert "no archived sessions" in capsys.readouterr().out


def test_archives_lists_recent_descending(monkeypatch, capsys, isolated):
    _seed_archive_rows(n=3)
    assert _run(["--archives"], monkeypatch) == 0
    out = capsys.readouterr().out
    # Most-recent first: "question number 2" should appear before "0".
    idx2 = out.find("answer body 2")
    idx0 = out.find("answer body 0")
    assert idx2 != -1 and idx0 != -1
    assert idx2 < idx0


def test_archives_respects_limit(monkeypatch, capsys, isolated):
    _seed_archive_rows(n=5)
    assert _run(["--archives", "2"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert out.count("answer body") == 2


def test_archives_limit_must_be_int(monkeypatch, capsys, isolated):
    assert _run(["--archives", "abc"], monkeypatch) == 2
    assert "integer" in capsys.readouterr().err


def test_archives_limit_must_be_positive(monkeypatch, capsys, isolated):
    assert _run(["--archives", "0"], monkeypatch) == 2
    assert "positive" in capsys.readouterr().err


def test_archives_filter_by_cmd(monkeypatch, capsys, isolated):
    _seed_archive_rows(n=2, cmd="ask")
    _seed_archive_rows(n=2, cmd="comma")
    assert _run(["--ask", "--archives"], monkeypatch) == 0
    out = capsys.readouterr().out
    # All shown rows must be `ask` — comma rows excluded.
    assert "ask" in out
    assert "comma" not in out


def test_archives_filter_empty_message(monkeypatch, capsys, isolated):
    assert _run(["--comma", "--archives"], monkeypatch) == 0
    assert "in `comma` sessions" in capsys.readouterr().out


def test_archives_too_many_args_errors(monkeypatch, capsys, isolated):
    assert _run(["--archives", "10", "20"], monkeypatch) == 2
    assert "at most one count" in capsys.readouterr().err


# ── --show --------------------------------------------------------------


def test_show_requires_id(monkeypatch, capsys, isolated):
    assert _run(["--show"], monkeypatch) == 2
    assert "needs an archive id" in capsys.readouterr().err


def test_show_id_must_be_int(monkeypatch, capsys, isolated):
    assert _run(["--show", "abc"], monkeypatch) == 2
    assert "integer" in capsys.readouterr().err


def test_show_missing_id_errors(monkeypatch, capsys, isolated):
    assert _run(["--show", "999"], monkeypatch) == 2
    assert "no archive with id" in capsys.readouterr().err


def test_show_prints_full_transcript(monkeypatch, capsys, isolated):
    _seed_archive_rows(n=1)
    assert _run(["--show", "1"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "question number 0" in out
    assert "answer body 0" in out
    # Header shows cmd, turn count, pwd.
    assert "ask" in out
    assert "/tmp/p0" in out


def test_show_too_many_args_errors(monkeypatch, capsys, isolated):
    assert _run(["--show", "1", "2"], monkeypatch) == 2
    assert "takes one id" in capsys.readouterr().err


def test_show_with_filter_does_not_apply(monkeypatch, capsys, isolated):
    """--show is global; combining it with --ask shouldn't be valid since
    facts/single-row reads aren't per-command operations."""
    _seed_archive_rows(n=1)
    assert _run(["--ask", "--show", "1"], monkeypatch) == 2
    assert "only apply to recall" in capsys.readouterr().err


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
