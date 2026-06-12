"""`?` terminal-context + piped-stdin plumbing (run_cli → run_agent)."""

from __future__ import annotations

import io
import sys

import pytest

from shellllm import ask


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLLM_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("SHELLLM_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("SHELLLM_MEMORY_FILE", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("TERM_SESSION_ID", "test-pane-ctx")
    monkeypatch.delenv("SHELLLM_SHELL_CONTEXT", raising=False)
    return tmp_path


@pytest.fixture
def captured_agent(monkeypatch):
    captured: dict = {}

    def fake_run_agent(prompt, **kwargs):
        captured["prompt"] = prompt
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(ask, "run_agent", fake_run_agent)
    return captured


class _TtyStdin(io.StringIO):
    def isatty(self):
        return True


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["shellllm-ask", *argv])
    return ask.main()


def test_tty_stdin_means_no_piped_input(monkeypatch, isolated, captured_agent):
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))
    assert _run(["what", "is", "foo"], monkeypatch) == 0
    assert captured_agent["piped_input"] == ""


def test_piped_stdin_is_forwarded(monkeypatch, isolated, captured_agent):
    monkeypatch.setattr(sys, "stdin", io.StringIO("error: segfault at line 3"))
    assert _run(["what", "broke"], monkeypatch) == 0
    assert "segfault at line 3" in captured_agent["piped_input"]


def test_no_ctx_flag_forwarded(monkeypatch, isolated, captured_agent):
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))
    assert _run(["--no-ctx", "what", "is", "foo"], monkeypatch) == 0
    assert captured_agent["shell_ctx"] is False
    assert captured_agent["prompt"] == "what is foo"


def test_shell_ctx_defaults_on(monkeypatch, isolated, captured_agent):
    monkeypatch.setattr(sys, "stdin", _TtyStdin(""))
    assert _run(["what", "is", "foo"], monkeypatch) == 0
    assert captured_agent["shell_ctx"] is True
