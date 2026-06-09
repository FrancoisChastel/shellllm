"""Verify ``SHELLLM_API_KEY`` / ``SHELLLM_EMBED_API_KEY`` add Bearer auth.

Covers the BYOK path that lets shellllm point at hosted OpenAI-compatible
endpoints (OpenAI, OpenRouter, Groq, Together, …) instead of a local
llama-server.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shellllm import client, embed


@dataclass
class FakeResponse:
    status_code: int = 200
    body: dict[str, Any] | None = None
    text: str = ""

    def json(self) -> Any:
        return self.body if self.body is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


# ── client._auth_headers ----------------------------------------------------


def test_auth_headers_no_key(monkeypatch):
    monkeypatch.delenv("SHELLLM_API_KEY", raising=False)
    headers = client._auth_headers()
    assert headers["Content-Type"] == "application/json"
    assert "Authorization" not in headers


def test_auth_headers_with_key(monkeypatch):
    monkeypatch.setenv("SHELLLM_API_KEY", "sk-test-123")
    headers = client._auth_headers()
    assert headers["Authorization"] == "Bearer sk-test-123"


def test_auth_headers_strips_whitespace(monkeypatch):
    monkeypatch.setenv("SHELLLM_API_KEY", "  sk-test-123\n")
    headers = client._auth_headers()
    assert headers["Authorization"] == "Bearer sk-test-123"


def test_auth_headers_empty_key_omits_auth(monkeypatch):
    monkeypatch.setenv("SHELLLM_API_KEY", "   ")
    headers = client._auth_headers()
    assert "Authorization" not in headers


# ── client.chat sends the header ------------------------------------------


def test_chat_sends_bearer_when_key_set(monkeypatch):
    monkeypatch.setenv("SHELLLM_API_KEY", "sk-test")
    captured: list[dict] = []

    def fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(
            status_code=200,
            body={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        )

    monkeypatch.setattr(client.httpx, "post", fake_post)
    msg = client.chat(
        [{"role": "user", "content": "hello"}],
        base_url="https://api.example.com",
    )
    assert msg["content"] == "hi"
    assert captured[0]["url"] == "https://api.example.com/v1/chat/completions"
    assert captured[0]["headers"]["Authorization"] == "Bearer sk-test"


def test_chat_no_auth_header_without_key(monkeypatch):
    monkeypatch.delenv("SHELLLM_API_KEY", raising=False)
    captured: list[dict] = []

    def fake_post(url, *, json, headers, timeout):  # noqa: A002
        captured.append({"headers": headers})
        return FakeResponse(
            status_code=200,
            body={"choices": [{"message": {"role": "assistant", "content": "hi"}}]},
        )

    monkeypatch.setattr(client.httpx, "post", fake_post)
    client.chat([{"role": "user", "content": "hello"}])
    assert "Authorization" not in captured[0]["headers"]


# ── embed._auth_headers --------------------------------------------------


def test_embed_auth_prefers_embed_specific_key(monkeypatch):
    monkeypatch.setenv("SHELLLM_API_KEY", "fallback-key")
    monkeypatch.setenv("SHELLLM_EMBED_API_KEY", "embed-key")
    headers = embed._auth_headers()
    assert headers["Authorization"] == "Bearer embed-key"


def test_embed_auth_falls_back_to_shared_key(monkeypatch):
    monkeypatch.delenv("SHELLLM_EMBED_API_KEY", raising=False)
    monkeypatch.setenv("SHELLLM_API_KEY", "shared-key")
    headers = embed._auth_headers()
    assert headers["Authorization"] == "Bearer shared-key"


def test_embed_auth_no_key(monkeypatch):
    monkeypatch.delenv("SHELLLM_API_KEY", raising=False)
    monkeypatch.delenv("SHELLLM_EMBED_API_KEY", raising=False)
    headers = embed._auth_headers()
    assert "Authorization" not in headers


def test_embed_sends_bearer_when_key_set(monkeypatch):
    monkeypatch.setenv("SHELLLM_API_KEY", "shared-key")
    monkeypatch.setenv("SHELLLM_EMBED_URL", "https://api.example.com")
    captured: list[dict] = []

    def fake_post(url, *, json, headers=None, timeout=None):  # noqa: A002
        captured.append({"url": url, "headers": headers or {}})
        return FakeResponse(body={"data": [{"embedding": [1.0, 0.0]}]})

    monkeypatch.setattr(embed.httpx, "post", fake_post)
    vec = embed.embed("hello", normalize_output=False)
    assert vec == [1.0, 0.0]
    assert captured[0]["headers"]["Authorization"] == "Bearer shared-key"


# ── web.fetch_url_as_text picks render first when configured -------------


def test_fetch_url_uses_render_when_configured(monkeypatch):
    from shellllm import web

    monkeypatch.setenv("SHELLLM_RENDER_URL", "https://render.test")
    monkeypatch.setenv("SHELLLM_RENDER_API_KEY", "fc-key")

    rendered_called = {"hit": False}
    static_called = {"hit": False}

    def fake_render(url):
        rendered_called["hit"] = True
        return "RENDERED markdown body"

    def fake_static(url):
        static_called["hit"] = True
        return "STATIC fallback"

    monkeypatch.setattr("shellllm.render.render_url", fake_render)
    monkeypatch.setattr(web, "fetch_url", fake_static)

    out = web.fetch_url_as_text("https://example.com/spa")
    assert "RENDERED" in out
    assert rendered_called["hit"]
    assert not static_called["hit"]


def test_fetch_url_falls_back_to_static_when_render_returns_none(monkeypatch):
    from shellllm import web

    def fake_render(url):
        return None

    def fake_static(url):
        return "STATIC body"

    monkeypatch.setattr("shellllm.render.render_url", fake_render)
    monkeypatch.setattr(web, "fetch_url", fake_static)

    out = web.fetch_url_as_text("https://example.com/static")
    assert "STATIC" in out
