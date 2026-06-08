"""Confirm `? --web` swaps to the web-first system prompt for one turn."""

from __future__ import annotations

import sys

import pytest

from shellllm import ask


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("SHELLLM_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.setenv("SHELLLM_ARCHIVE_DB", str(tmp_path / "archive.db"))
    monkeypatch.setenv("SHELLLM_MEMORY_FILE", str(tmp_path / "memory.jsonl"))
    monkeypatch.setenv("TERM_SESSION_ID", "test-pane-web")
    return tmp_path


def test_web_flag_swaps_system_prompt(monkeypatch, isolated):
    captured: dict[str, str] = {}

    def fake_run_agent(prompt, *, system, **kwargs):
        captured["system"] = system
        captured["prompt"] = prompt
        return 0

    monkeypatch.setattr(ask, "run_agent", fake_run_agent)
    monkeypatch.setattr(sys, "argv", ["shellllm-ask", "--web", "latest", "ripgrep"])
    assert ask.main() == 0
    assert captured["system"] == ask.ASK_WEB_SYSTEM
    assert captured["prompt"] == "latest ripgrep"


def test_short_web_flag_works(monkeypatch, isolated):
    captured: dict[str, str] = {}

    def fake_run_agent(prompt, *, system, **kwargs):
        captured["system"] = system
        return 0

    monkeypatch.setattr(ask, "run_agent", fake_run_agent)
    monkeypatch.setattr(sys, "argv", ["shellllm-ask", "-w", "what is bar"])
    assert ask.main() == 0
    assert captured["system"] == ask.ASK_WEB_SYSTEM


def test_no_web_flag_uses_ask_system(monkeypatch, isolated):
    captured: dict[str, str] = {}

    def fake_run_agent(prompt, *, system, **kwargs):
        captured["system"] = system
        return 0

    monkeypatch.setattr(ask, "run_agent", fake_run_agent)
    monkeypatch.setattr(sys, "argv", ["shellllm-ask", "what is bar"])
    assert ask.main() == 0
    assert captured["system"] == ask.ASK_SYSTEM
