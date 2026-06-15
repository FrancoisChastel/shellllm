"""Tests for the CLI surface around archive lifecycle: --prune / --vacuum."""

from __future__ import annotations

import sys
import time

import pytest

from shellllm import recall
from shellllm.archive import Archive
from shellllm.recall import _parse_age_spec


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLLM_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("SHELLLM_MEMORY_FILE", str(tmp_path / "memory.jsonl"))
    return tmp_path


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["shellllm-recall", *argv])
    return recall.main()


def _seed(when_offsets_sec: list[float]):
    archive = Archive()
    now = time.time()
    for off in when_offsets_sec:
        archive.ingest_session(
            cmd="ask",
            terminal_id="t",
            created_at=now + off,
            last_used=now + off,
            last_pwd="/tmp",
            last_date="2026-06-01",
            turn_count=1,
            messages=[
                {"role": "user", "content": f"q@{off}"},
                {"role": "assistant", "content": f"a@{off}"},
            ],
            archived_at=now + off,
        )


@pytest.mark.parametrize(
    "spec, seconds",
    [
        ("30s", 30),
        ("5m", 300),
        ("12h", 12 * 3600),
        ("90d", 90 * 86_400),
        ("2w", 2 * 7 * 86_400),
    ],
)
def test_age_spec_parses(spec, seconds):
    assert _parse_age_spec(spec) == pytest.approx(seconds)


@pytest.mark.parametrize("spec", ["", "30", "abc", "30x", "d", "10.5z"])
def test_age_spec_rejects_garbage(spec):
    assert _parse_age_spec(spec) is None


def test_prune_older_than_via_cli(monkeypatch, capsys, isolated):
    _seed([-86_400 * 100, -86_400 * 10, 0])  # 100d / 10d / now
    assert _run(["--prune", "--older-than", "60d"], monkeypatch) == 0
    assert "pruned 1" in capsys.readouterr().out
    assert Archive().count() == 2


def test_prune_keep_via_cli(monkeypatch, capsys, isolated):
    _seed([0, -1, -2, -3, -4])
    assert _run(["--prune", "--keep", "2"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "pruned 3" in out
    assert Archive().count() == 2


def test_prune_age_and_keep_combined(monkeypatch, capsys, isolated):
    _seed([-86_400 * 100, -86_400 * 100, 0, -1, -2])
    # First drop the >60d old (2 rows), then keep only 2 of the remaining 3.
    assert _run(["--prune", "--older-than", "60d", "--keep", "2"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "pruned 3" in out
    assert Archive().count() == 2


def test_prune_without_args_errors(monkeypatch, capsys, isolated):
    assert _run(["--prune"], monkeypatch) == 2
    err = capsys.readouterr().err
    assert "--older-than" in err
    assert "--keep" in err


def test_prune_rejects_garbage_older_than(monkeypatch, capsys, isolated):
    assert _run(["--prune", "--older-than", "abc"], monkeypatch) == 2


def test_prune_rejects_negative_keep(monkeypatch, capsys, isolated):
    assert _run(["--prune", "--keep", "-3"], monkeypatch) == 2


def test_vacuum_runs(monkeypatch, capsys, isolated):
    _seed([0])
    assert _run(["--vacuum"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "vacuum done" in out


def test_status_shows_size(monkeypatch, capsys, isolated):
    _seed([0])
    assert _run(["--status"], monkeypatch) == 0
    out = capsys.readouterr().out
    assert "MB on disk" in out


def test_prune_rejected_with_filter_flag(monkeypatch, capsys, isolated):
    """`--prune` is global; combining with --ask/--comma must error."""
    assert _run(["--prune", "--keep", "10", "--ask"], monkeypatch) == 2
