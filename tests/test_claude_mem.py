"""Tests for the optional claude-mem server-beta adapter.

We never touch the network — all HTTP is captured via a mock at the
``httpx.post`` boundary so the suite stays fast and offline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from shellllm import claude_mem as mem
from shellllm.claude_mem import (
    API_KEY_ENV,
    BASE_URL_ENV,
    ENABLE_ENV,
    PROJECT_ID_ENV,
    ClaudeMemAdapter,
    render_context_block,
)


@dataclass
class FakeResponse:
    """Just enough of httpx.Response to satisfy ``_post``."""

    status_code: int = 200
    body: dict[str, Any] | None = None

    @property
    def content(self) -> bytes:
        return b"{}" if self.body is not None else b""

    def json(self) -> Any:
        return self.body if self.body is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def captured_posts(monkeypatch):
    """Patch httpx.post in the claude_mem module; return the capture list."""
    calls: list[dict[str, Any]] = []

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002
        calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(body={})

    monkeypatch.setattr(mem.httpx, "post", _fake_post)
    return calls


@pytest.fixture
def configured_env(monkeypatch):
    monkeypatch.setenv(BASE_URL_ENV, "https://example.test")
    monkeypatch.setenv(API_KEY_ENV, "test-key")
    monkeypatch.setenv(PROJECT_ID_ENV, "proj-1")
    monkeypatch.delenv(ENABLE_ENV, raising=False)


# ── Configuration / enable ─────────────────────────────────────────


def test_adapter_disabled_when_no_config(monkeypatch):
    for v in (BASE_URL_ENV, API_KEY_ENV, PROJECT_ID_ENV, ENABLE_ENV):
        monkeypatch.delenv(v, raising=False)
    adapter = ClaudeMemAdapter()
    assert not adapter.configured
    assert not adapter.enabled


def test_adapter_enabled_when_all_three_envs_set(configured_env):
    adapter = ClaudeMemAdapter()
    assert adapter.configured
    assert adapter.enabled


def test_opt_out_via_env_disables_even_when_configured(configured_env, monkeypatch):
    monkeypatch.setenv(ENABLE_ENV, "0")
    adapter = ClaudeMemAdapter()
    assert adapter.configured
    assert not adapter.enabled


def test_override_true_requires_configured(monkeypatch):
    # An explicit on-override must still respect missing config.
    for v in (BASE_URL_ENV, API_KEY_ENV, PROJECT_ID_ENV, ENABLE_ENV):
        monkeypatch.delenv(v, raising=False)
    adapter = ClaudeMemAdapter(enabled_override=True)
    assert not adapter.enabled


def test_override_false_disables_when_configured(configured_env):
    adapter = ClaudeMemAdapter(enabled_override=False)
    assert not adapter.enabled


# ── Writes ─────────────────────────────────────────────────────────


def test_record_observation_async_posts_expected_payload(configured_env, captured_posts):
    adapter = ClaudeMemAdapter()
    thread = adapter.record_observation_async("hello world", kind="test")
    assert thread is not None
    thread.join(timeout=2)
    assert len(captured_posts) == 1
    call = captured_posts[0]
    assert call["url"] == "https://example.test/v1/memories"
    assert call["json"]["projectId"] == "proj-1"
    assert call["json"]["kind"] == "test"
    assert call["json"]["type"] == "test"
    assert call["json"]["narrative"] == "hello world"
    assert call["headers"]["Authorization"] == "Bearer test-key"


def test_record_observation_noop_when_disabled(monkeypatch, captured_posts):
    for v in (BASE_URL_ENV, API_KEY_ENV, PROJECT_ID_ENV):
        monkeypatch.delenv(v, raising=False)
    adapter = ClaudeMemAdapter()
    assert adapter.record_observation_async("hi") is None
    assert captured_posts == []


def test_record_observation_skips_empty_content(configured_env, captured_posts):
    adapter = ClaudeMemAdapter()
    assert adapter.record_observation_async("   ") is None
    assert captured_posts == []


def test_record_observation_swallows_errors(configured_env, monkeypatch):
    def _broken(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(mem.httpx, "post", _broken)
    adapter = ClaudeMemAdapter()
    # Must return a thread, complete cleanly, and never raise.
    t = adapter.record_observation_async("anything")
    assert t is not None
    t.join(timeout=2)
    assert not t.is_alive()


def test_record_observation_attaches_metadata(configured_env, captured_posts):
    adapter = ClaudeMemAdapter()
    t = adapter.record_observation_async(
        "fact",
        kind="user-fact",
        metadata={"source": "shellllm --remember"},
    )
    t.join(timeout=2)
    assert captured_posts[0]["json"]["metadata"] == {"source": "shellllm --remember"}


# ── Reads ──────────────────────────────────────────────────────────


def test_query_context_returns_pre_joined_string(configured_env, monkeypatch):
    captured: list[dict[str, Any]] = []

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured.append({"url": url, "json": json})
        return FakeResponse(body={"context": "prior session note"})

    monkeypatch.setattr(mem.httpx, "post", _fake_post)
    adapter = ClaudeMemAdapter()
    out = adapter.query_context("how to use ripgrep")
    assert out == "prior session note"
    assert captured[0]["url"] == "https://example.test/v1/context"
    assert captured[0]["json"]["query"] == "how to use ripgrep"
    assert captured[0]["json"]["limit"] == 5


def test_query_context_falls_back_to_stitching_observations(configured_env, monkeypatch):
    """If the server returns a list without a pre-joined context, we
    stitch narratives ourselves so older versions still work."""

    def _fake_post(url, *, json, headers, timeout):  # noqa: A002
        return FakeResponse(
            body={
                "observations": [
                    {"narrative": "alpha"},
                    {"content": "beta"},
                    {"text": "gamma"},
                    {"unrelated": "skipped"},
                ]
            }
        )

    monkeypatch.setattr(mem.httpx, "post", _fake_post)
    adapter = ClaudeMemAdapter()
    out = adapter.query_context("q")
    assert out is not None
    assert "alpha" in out
    assert "beta" in out
    assert "gamma" in out


def test_query_context_returns_none_when_disabled(monkeypatch):
    for v in (BASE_URL_ENV, API_KEY_ENV, PROJECT_ID_ENV):
        monkeypatch.delenv(v, raising=False)
    adapter = ClaudeMemAdapter()
    assert adapter.query_context("anything") is None


def test_query_context_swallows_errors(configured_env, monkeypatch):
    def _broken(*args, **kwargs):
        raise RuntimeError("nope")

    monkeypatch.setattr(mem.httpx, "post", _broken)
    adapter = ClaudeMemAdapter()
    assert adapter.query_context("q") is None


def test_query_context_empty_query_returns_none(configured_env, captured_posts):
    adapter = ClaudeMemAdapter()
    assert adapter.query_context("  ") is None
    assert captured_posts == []


# ── Rendering ──────────────────────────────────────────────────────


def test_render_context_block_wraps_in_tag():
    out = render_context_block("alpha\nbeta")
    assert out.startswith("<claude-mem-context>")
    assert out.endswith("</claude-mem-context>")
    assert "alpha" in out
    assert "beta" in out


def test_render_context_block_empty_text_returns_empty():
    assert render_context_block("") == ""
    assert render_context_block("   ") == ""
