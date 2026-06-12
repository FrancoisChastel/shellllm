"""Tests for the optional JS-rendering adapter (``render.render_url``).

httpx is mocked at the module boundary; no real network is touched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from shellllm import render


@dataclass
class FakeResponse:
    status_code: int = 200
    body: Any | None = None

    def json(self) -> Any:
        return self.body if self.body is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setenv(render.RENDER_URL_ENV, "https://render.test")
    monkeypatch.setenv(render.RENDER_KEY_ENV, "test-key")


@pytest.fixture
def unset_env(monkeypatch):
    monkeypatch.delenv(render.RENDER_URL_ENV, raising=False)
    monkeypatch.delenv(render.RENDER_KEY_ENV, raising=False)
    monkeypatch.delenv(render.RENDER_TIMEOUT_ENV, raising=False)


# ── Configuration -----------------------------------------------------------


def test_is_configured_requires_both_vars(monkeypatch, unset_env):
    assert render.is_configured() is False
    monkeypatch.setenv(render.RENDER_URL_ENV, "https://x")
    assert render.is_configured() is False
    monkeypatch.setenv(render.RENDER_KEY_ENV, "k")
    assert render.is_configured() is True


def test_render_url_noop_when_not_configured(monkeypatch, unset_env):
    captured: list = []
    monkeypatch.setattr(render.httpx, "post", lambda *a, **kw: captured.append(a))
    assert render.render_url("https://example.com") is None
    assert captured == []


def test_render_url_empty_input_returns_none(configured, monkeypatch):
    monkeypatch.setattr(
        render.httpx,
        "post",
        lambda *a, **kw: pytest.fail("should not POST for empty url"),
    )
    assert render.render_url("   ") is None


# ── Happy path --------------------------------------------------------------


def test_render_url_posts_firecrawl_payload(configured, monkeypatch):
    captured: list[dict] = []

    def fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return FakeResponse(body={"data": {"markdown": "# rendered"}})

    monkeypatch.setattr(render.httpx, "post", fake_post)

    out = render.render_url("https://example.com/spa")
    assert out == "# rendered"
    call = captured[0]
    assert call["url"] == "https://render.test/v1/scrape"
    assert call["json"] == {"url": "https://example.com/spa", "formats": ["markdown"]}
    assert call["headers"]["Authorization"] == "Bearer test-key"
    assert call["headers"]["Content-Type"] == "application/json"


def test_render_url_falls_through_response_fields(configured, monkeypatch):
    """We tolerate older Firecrawl-shape responses that only expose html."""

    monkeypatch.setattr(
        render.httpx,
        "post",
        lambda *a, **kw: FakeResponse(body={"data": {"html": "<h1>hi</h1>"}}),
    )
    assert render.render_url("https://example.com") == "<h1>hi</h1>"


def test_render_url_supports_top_level_markdown(configured, monkeypatch):
    """Some implementations skip the `data` wrapper."""

    monkeypatch.setattr(
        render.httpx,
        "post",
        lambda *a, **kw: FakeResponse(body={"markdown": "top-level"}),
    )
    assert render.render_url("https://example.com") == "top-level"


# ── Failure modes -----------------------------------------------------------


def test_render_url_returns_none_on_http_error(configured, monkeypatch):
    monkeypatch.setattr(
        render.httpx,
        "post",
        lambda *a, **kw: FakeResponse(status_code=502),
    )
    assert render.render_url("https://example.com") is None


def test_render_url_returns_none_on_connection_error(configured, monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("connect refused")

    monkeypatch.setattr(render.httpx, "post", broken)
    assert render.render_url("https://example.com") is None


def test_render_url_returns_none_on_malformed_json(configured, monkeypatch):
    monkeypatch.setattr(
        render.httpx,
        "post",
        lambda *a, **kw: FakeResponse(body={"unrelated": "shape"}),
    )
    assert render.render_url("https://example.com") is None


def test_render_url_returns_none_on_non_dict_payload(configured, monkeypatch):
    monkeypatch.setattr(
        render.httpx,
        "post",
        lambda *a, **kw: FakeResponse(body=["not", "a", "dict"]),
    )
    assert render.render_url("https://example.com") is None


# ── Custom timeout ---------------------------------------------------------


def test_render_url_honors_timeout_env(configured, monkeypatch):
    monkeypatch.setenv(render.RENDER_TIMEOUT_ENV, "5")
    captured: list[float] = []

    def fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured.append(timeout)
        return FakeResponse(body={"data": {"markdown": "ok"}})

    monkeypatch.setattr(render.httpx, "post", fake_post)
    render.render_url("https://example.com")
    assert captured[0] == 5.0


def test_render_url_bad_timeout_env_falls_back_to_default(configured, monkeypatch):
    monkeypatch.setenv(render.RENDER_TIMEOUT_ENV, "not-a-number")
    captured: list[float] = []

    def fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured.append(timeout)
        return FakeResponse(body={"data": {"markdown": "ok"}})

    monkeypatch.setattr(render.httpx, "post", fake_post)
    render.render_url("https://example.com")
    assert captured[0] == render.DEFAULT_TIMEOUT
