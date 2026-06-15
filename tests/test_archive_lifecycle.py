"""Tests for archive lifecycle: prune by age, prune to keep N, vacuum."""

from __future__ import annotations

import time

import pytest

from shellllm.archive import Archive


@pytest.fixture
def archive(tmp_path):
    db = tmp_path / "archive.db"
    return Archive(path=db)


def _ingest(archive: Archive, *, when: float, label: str = "ask") -> int | None:
    return archive.ingest_session(
        cmd=label,
        terminal_id="t",
        created_at=when,
        last_used=when,
        last_pwd="/tmp",
        last_date="2026-06-01",
        turn_count=1,
        messages=[
            {"role": "user", "content": f"q at {when}"},
            {"role": "assistant", "content": f"a at {when}"},
        ],
        archived_at=when,
    )


def test_db_size_bytes_grows_with_ingestion(archive):
    """A few inserts must push the file past sqlite's page preallocation."""
    before = archive.db_size_bytes()
    for i in range(100):
        _ingest(archive, when=time.time() + i)
    assert archive.db_size_bytes() > before


def test_prune_older_than_drops_only_old_rows(archive):
    now = time.time()
    _ingest(archive, when=now - 86_400 * 100)  # 100 days old
    _ingest(archive, when=now - 86_400 * 30)  # 30 days old
    _ingest(archive, when=now)  # fresh
    assert archive.count() == 3

    deleted = archive.prune_older_than(cutoff=now - 86_400 * 60)
    assert deleted == 1
    assert archive.count() == 2


def test_prune_older_than_returns_zero_when_nothing_old(archive):
    _ingest(archive, when=time.time())
    assert archive.prune_older_than(cutoff=time.time() - 86_400 * 365) == 0
    assert archive.count() == 1


def test_prune_to_keep_newest_keeps_most_recent(archive):
    base = time.time() - 1000
    for i in range(10):
        _ingest(archive, when=base + i)
    assert archive.count() == 10

    deleted = archive.prune_to_keep_newest(keep=3)
    assert deleted == 7
    assert archive.count() == 3

    # The three kept rows must be the three newest.
    hits = archive.recent(limit=10)
    timestamps = sorted(h.archived_at for h in hits)
    assert timestamps == sorted([base + 7, base + 8, base + 9])


def test_prune_to_keep_newest_handles_keep_zero(archive):
    _ingest(archive, when=time.time())
    _ingest(archive, when=time.time())
    deleted = archive.prune_to_keep_newest(keep=0)
    assert deleted == 2
    assert archive.count() == 0


def test_prune_to_keep_newest_noop_when_under_cap(archive):
    _ingest(archive, when=time.time())
    _ingest(archive, when=time.time())
    deleted = archive.prune_to_keep_newest(keep=10)
    assert deleted == 0
    assert archive.count() == 2


def test_vacuum_safe_on_empty_db(archive):
    archive.vacuum()  # must not raise
    assert archive.count() == 0


def test_vacuum_reduces_size_after_prune(archive, tmp_path):
    """After deleting many rows, vacuum should reclaim measurable bytes."""
    now = time.time()
    # Ingest enough to make a measurable difference (each session ~200B+).
    for i in range(50):
        _ingest(archive, when=now - i)
    before = archive.db_size_bytes()

    archive.prune_to_keep_newest(keep=1)
    archive.vacuum()
    after = archive.db_size_bytes()

    assert after < before, f"vacuum should reclaim space ({before} → {after})"
