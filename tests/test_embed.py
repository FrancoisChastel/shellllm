"""Tests for the embedding client + storage helpers."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass
from typing import Any

import pytest

from shellllm import embed as emb


@dataclass
class FakeResponse:
    status_code: int = 200
    body: dict[str, Any] | None = None

    def json(self) -> Any:
        return self.body or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_normalize_unit_norm():
    out = emb.normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, abs_tol=1e-6)


def test_normalize_zero_vector_returns_input():
    assert emb.normalize([0.0, 0.0, 0.0]) == [0.0, 0.0, 0.0]


def test_pack_unpack_round_trips_floats():
    vec = [0.1, -0.5, 0.7, 1e-3]
    packed = emb.pack_embedding(vec)
    assert len(packed) == 4 * len(vec)
    out = emb.unpack_embedding(packed)
    assert all(math.isclose(a, b, abs_tol=1e-6) for a, b in zip(vec, out, strict=True))


def test_unpack_handles_empty_blob():
    assert emb.unpack_embedding(b"") == []


def test_pack_uses_little_endian_fp32():
    """Format is part of the on-disk contract — pin it."""
    packed = emb.pack_embedding([1.0])
    assert packed == struct.pack("<f", 1.0)


def test_cosine_same_vector_is_one():
    assert math.isclose(emb.cosine([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]), 1.0, abs_tol=1e-6)


def test_cosine_orthogonal_is_zero():
    assert math.isclose(emb.cosine([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-6)


def test_cosine_mismatched_dim_returns_zero():
    assert emb.cosine([1.0, 0.0], [1.0, 0.0, 0.0]) == 0.0


def test_cosine_zero_vector_returns_zero():
    assert emb.cosine([0.0, 0.0], [1.0, 1.0]) == 0.0


def test_embed_posts_to_server_and_normalizes(monkeypatch):
    captured: list[dict[str, Any]] = []

    def fake_post(url, *, json, headers=None, timeout):  # noqa: A002
        captured.append({"url": url, "json": json, "headers": headers or {}, "timeout": timeout})
        return FakeResponse(body={"data": [{"embedding": [3.0, 4.0]}]})

    monkeypatch.setenv("SHELLLM_EMBED_URL", "http://embed.test")
    monkeypatch.setattr(emb.httpx, "post", fake_post)

    out = emb.embed("hello")
    assert out is not None
    assert captured[0]["url"] == "http://embed.test/v1/embeddings"
    assert captured[0]["json"]["input"] == "hello"
    # Output normalized.
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, abs_tol=1e-6)


def test_embed_returns_none_on_empty_input():
    assert emb.embed("   ") is None


def test_embed_returns_none_on_connection_error(monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(emb.httpx, "post", broken)
    assert emb.embed("hello") is None


def test_embed_returns_none_on_malformed_response(monkeypatch):
    monkeypatch.setattr(emb.httpx, "post", lambda *a, **kw: FakeResponse(body={"oops": "no data"}))
    assert emb.embed("hello") is None


def test_embed_returns_none_when_embedding_is_not_list(monkeypatch):
    monkeypatch.setattr(
        emb.httpx,
        "post",
        lambda *a, **kw: FakeResponse(body={"data": [{"embedding": "not-a-list"}]}),
    )
    assert emb.embed("hello") is None


def test_is_available_true_on_200(monkeypatch):
    monkeypatch.setattr(emb.httpx, "get", lambda *a, **kw: FakeResponse(status_code=200))
    assert emb.is_available() is True


def test_is_available_false_on_error(monkeypatch):
    def broken(*a, **kw):
        raise RuntimeError("down")

    monkeypatch.setattr(emb.httpx, "get", broken)
    assert emb.is_available() is False


def test_is_available_false_on_404(monkeypatch):
    monkeypatch.setattr(emb.httpx, "get", lambda *a, **kw: FakeResponse(status_code=404))
    assert emb.is_available() is False


@pytest.mark.parametrize("base", ["http://x.test/", "http://x.test"])
def test_base_url_trailing_slash_normalised(monkeypatch, base):
    monkeypatch.setenv("SHELLLM_EMBED_URL", base)
    captured: list[str] = []
    monkeypatch.setattr(
        emb.httpx,
        "post",
        lambda url, **kw: (
            captured.append(url),
            FakeResponse(body={"data": [{"embedding": [1.0]}]}),
        )[1],
    )
    emb.embed("hello")
    assert captured[0] == "http://x.test/v1/embeddings"
