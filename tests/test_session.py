"""Tests for the per-terminal conversation session store."""

from __future__ import annotations

import os
import time

import pytest

from shellllm.session import (
    IDLE_TTL_SECONDS,
    SessionStore,
    derive_terminal_id,
    sweep_expired,
)


@pytest.fixture
def sessions_dir(tmp_path):
    return tmp_path / "sessions"


def test_terminal_id_is_stable_within_process(monkeypatch):
    monkeypatch.setenv("TERM_SESSION_ID", "abc-123")
    assert derive_terminal_id() == derive_terminal_id()


def test_terminal_id_differs_per_pane(monkeypatch):
    monkeypatch.setenv("TERM_SESSION_ID", "abc-123")
    a = derive_terminal_id()
    monkeypatch.setenv("TERM_SESSION_ID", "def-456")
    b = derive_terminal_id()
    assert a != b


def test_terminal_id_falls_back_to_tmux(monkeypatch):
    monkeypatch.delenv("TERM_SESSION_ID", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%42")
    a = derive_terminal_id()
    monkeypatch.setenv("TMUX_PANE", "%99")
    b = derive_terminal_id()
    assert a != b


def test_terminal_id_falls_back_to_ppid(monkeypatch):
    monkeypatch.delenv("TERM_SESSION_ID", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.delenv("WINDOWID", raising=False)
    # Must still produce something deterministic.
    tid = derive_terminal_id()
    assert isinstance(tid, str)
    assert len(tid) == 12


def test_open_returns_fresh_store_when_no_file(sessions_dir):
    store, expired = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    assert not expired
    assert store.is_empty()
    assert store.cmd == "ask"


def test_write_then_open_round_trips_messages(sessions_dir):
    store, _ = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    store.extend(
        [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
    )
    store.touch(pwd="/tmp", date="2026-06-08")
    store.write()

    again, expired = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    assert not expired
    assert len(again.messages) == 2
    assert again.messages[0]["content"] == "hello"
    assert again.meta.last_pwd == "/tmp"
    assert again.meta.turn_count == 1


def test_idle_ttl_rotates_old_session(sessions_dir):
    store, _ = SessionStore.open(
        cmd="ask",
        terminal_id="t1",
        sessions_dir=sessions_dir,
        now=1000.0,
    )
    store.extend([{"role": "user", "content": "old"}])
    store.touch(pwd="/tmp", date="2026-06-01", now=1000.0)
    store.write()

    # Open again after TTL + 1 second has passed.
    fresh, expired = SessionStore.open(
        cmd="ask",
        terminal_id="t1",
        sessions_dir=sessions_dir,
        now=1000.0 + IDLE_TTL_SECONDS + 1,
    )
    assert expired
    assert fresh.is_empty()
    # And the old file is rotated, not deleted, so we can audit later.
    expired_files = list(sessions_dir.glob("ask-t1.jsonl.expired-*"))
    assert len(expired_files) == 1


def test_ask_and_search_have_separate_files(sessions_dir):
    ask_store, _ = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    search_store, _ = SessionStore.open(cmd="search", terminal_id="t1", sessions_dir=sessions_dir)
    assert ask_store.path != search_store.path
    ask_store.extend([{"role": "user", "content": "ask-only"}])
    ask_store.write()

    search_again, _ = SessionStore.open(cmd="search", terminal_id="t1", sessions_dir=sessions_dir)
    assert search_again.is_empty()


def test_archive_and_reset_rotates_file(sessions_dir):
    store, _ = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    store.extend([{"role": "user", "content": "x"}])
    store.write()
    assert store.path.exists()
    store.archive_and_reset()
    rotated = list(sessions_dir.glob("ask-t1.jsonl.expired-*"))
    assert len(rotated) == 1
    assert store.is_empty()


def test_reset_deletes_file(sessions_dir):
    store, _ = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    store.extend([{"role": "user", "content": "x"}])
    store.write()
    store.reset()
    assert not store.path.exists()
    assert store.is_empty()


def test_corrupt_line_is_tolerated(sessions_dir):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    path = sessions_dir / "ask-t1.jsonl"
    path.write_text(
        '{"_meta": {"created": 1, "last_used": 9999999999}}\n'
        "this is not json\n"
        '{"role": "user", "content": "after corrupt"}\n',
        encoding="utf-8",
    )
    store, _ = SessionStore.open(
        cmd="ask",
        terminal_id="t1",
        sessions_dir=sessions_dir,
        now=9999999999.5,
    )
    assert len(store.messages) == 1
    assert store.messages[0]["content"] == "after corrupt"


def test_archive_called_on_ttl_rotation(sessions_dir, tmp_path):
    """When a session expires, its transcript must reach the archive
    BEFORE the JSONL gets renamed aside, otherwise recall would lose it."""

    from shellllm.archive import Archive

    archive = Archive(path=tmp_path / "archive.db")

    store, _ = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir, now=1000.0)
    store.extend(
        [
            {"role": "user", "content": "an old question"},
            {"role": "assistant", "content": "an old answer"},
        ]
    )
    store.touch(pwd="/tmp", date="2026-05-01", now=1000.0)
    store.write()

    fresh, expired = SessionStore.open(
        cmd="ask",
        terminal_id="t1",
        sessions_dir=sessions_dir,
        now=1000.0 + IDLE_TTL_SECONDS + 1,
        archive=archive,
    )
    assert expired
    assert fresh.is_empty()
    assert archive.count() == 1
    hits = archive.search("old question")
    assert len(hits) == 1
    assert "old answer" in hits[0].content


def test_archive_and_reset_with_archive_persists_transcript(sessions_dir, tmp_path):
    from shellllm.archive import Archive

    archive = Archive(path=tmp_path / "archive.db")

    store, _ = SessionStore.open(cmd="ask", terminal_id="t1", sessions_dir=sessions_dir)
    store.extend(
        [
            {"role": "user", "content": "reset me"},
            {"role": "assistant", "content": "ok"},
        ]
    )
    store.archive_and_reset(archive=archive)
    assert archive.count() == 1
    assert store.is_empty()


def test_sweep_expired_deletes_old_rotated_files(sessions_dir, monkeypatch):
    sessions_dir.mkdir(parents=True, exist_ok=True)
    old = sessions_dir / "ask-t1.jsonl.expired-1"
    old.write_text("{}\n", encoding="utf-8")
    # Make it look ancient.
    very_old = time.time() - 60 * 24 * 3600
    os.utime(old, (very_old, very_old))

    fresh = sessions_dir / "ask-t2.jsonl.expired-2"
    fresh.write_text("{}\n", encoding="utf-8")

    removed = sweep_expired(sessions_dir, max_age_seconds=7 * 24 * 3600)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
