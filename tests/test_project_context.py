"""Tests for `.shellllmrc` discovery and rendering."""

from __future__ import annotations

from shellllm.project_context import (
    MAX_RC_BYTES,
    find_rc_file,
    read_rc_block,
)


def test_find_rc_in_cwd(tmp_path, monkeypatch):
    (tmp_path / ".shellllmrc").write_text("project uses pnpm")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert find_rc_file() == tmp_path / ".shellllmrc"


def test_find_rc_walks_up(tmp_path, monkeypatch):
    (tmp_path / ".shellllmrc").write_text("monorepo root")
    nested = tmp_path / "packages" / "api" / "src"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert find_rc_file() == tmp_path / ".shellllmrc"


def test_innermost_rc_wins(tmp_path, monkeypatch):
    (tmp_path / ".shellllmrc").write_text("monorepo root")
    inner = tmp_path / "packages" / "api"
    inner.mkdir(parents=True)
    (inner / ".shellllmrc").write_text("the api package")
    monkeypatch.chdir(inner)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert find_rc_file() == inner / ".shellllmrc"


def test_find_rc_returns_none_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # Make sure $HOME doesn't accidentally have one either.
    monkeypatch.setenv("HOME", str(tmp_path))
    assert find_rc_file() is None


def test_search_stops_at_home(tmp_path, monkeypatch):
    """We must never read a `.shellllmrc` outside the user's $HOME."""
    home = tmp_path / "home"
    home.mkdir()
    (home / ".shellllmrc").write_text("home rc")
    nested = home / "code" / "project"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)
    monkeypatch.setenv("HOME", str(home))
    # The HOME rc is found because cwd is inside HOME.
    assert find_rc_file() == home / ".shellllmrc"


def test_read_rc_block_renders_system_message(tmp_path, monkeypatch):
    rc = tmp_path / ".shellllmrc"
    rc.write_text("this project uses pnpm and TypeScript")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    block = read_rc_block()
    assert "Project context" in block
    assert ".shellllmrc" in block
    assert "this project uses pnpm and TypeScript" in block


def test_read_rc_block_empty_when_absent(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert read_rc_block() == ""


def test_read_rc_block_strips_blank_files(tmp_path, monkeypatch):
    (tmp_path / ".shellllmrc").write_text("   \n\n  ")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert read_rc_block() == ""


def test_read_rc_block_caps_huge_files(tmp_path, monkeypatch):
    """Oversized files get tail-truncated; the trailing sentinel survives."""
    sentinel = "FINAL_LINE_KEEP_ME"
    huge = ("Q" * (MAX_RC_BYTES * 2)) + sentinel
    (tmp_path / ".shellllmrc").write_text(huge)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    block = read_rc_block()
    # Body is capped at MAX_RC_BYTES; the trailing sentinel must survive.
    assert "truncated" in block
    assert sentinel in block
    # Header + body + footer fits comfortably within twice the cap.
    assert len(block) < (MAX_RC_BYTES * 2)
