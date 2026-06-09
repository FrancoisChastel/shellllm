"""Client for a local embedding server (llama-server in ``--embedding`` mode).

llama.cpp can serve embeddings on the same OpenAI-style endpoint
(``POST /v1/embeddings``) as completions, but a single process can
only do one or the other. So the convention here is a *second*
llama-server instance — typically on port 8081 — running a small
embedding model (e.g. Qwen3-Embedding-0.6B-GGUF or bge-small-en).
Point us at it with ``SHELLLM_EMBED_URL``.

All operations are no-ops with ``None`` returns if the server is
unreachable or refuses; this keeps the recall layer working in pure
FTS5 mode when no embedding model is available.

Storage helpers pack/unpack vectors as little-endian fp32 BLOBs so we
can stash them in sqlite without dragging in numpy.
"""

from __future__ import annotations

import math
import os
import struct

import httpx

DEFAULT_EMBED_URL = os.environ.get("SHELLLM_EMBED_URL", "http://127.0.0.1:8081")
DEFAULT_EMBED_MODEL = os.environ.get("SHELLLM_EMBED_MODEL", "local-embed")
DEFAULT_TIMEOUT = float(os.environ.get("SHELLLM_EMBED_TIMEOUT", "8"))
EMBED_API_KEY_ENV = "SHELLLM_EMBED_API_KEY"
FALLBACK_API_KEY_ENV = "SHELLLM_API_KEY"


# Lazy `os.environ.get` re-read so tests can monkeypatch the var.
def _base_url() -> str:
    return os.environ.get("SHELLLM_EMBED_URL", DEFAULT_EMBED_URL).rstrip("/")


def _auth_headers() -> dict[str, str]:
    """Bearer auth for hosted embedding endpoints.

    ``SHELLLM_EMBED_API_KEY`` takes precedence; if unset we fall back to
    ``SHELLLM_API_KEY`` so a single key powers chat + embeddings against
    the same provider. The local llama-server doesn't need either.
    """
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(EMBED_API_KEY_ENV, "").strip()
    if not key:
        key = os.environ.get(FALLBACK_API_KEY_ENV, "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def normalize(vec: list[float]) -> list[float]:
    """L2-normalize so retrieval reduces to a dot product at query time."""
    n = math.sqrt(sum(v * v for v in vec))
    if n == 0.0:
        return list(vec)
    return [v / n for v in vec]


def pack_embedding(vec: list[float]) -> bytes:
    """Little-endian fp32 byte blob — fits straight into a sqlite BLOB column."""
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_embedding(blob: bytes) -> list[float]:
    """Inverse of :func:`pack_embedding`. Assumes the writer used fp32."""
    n = len(blob) // 4
    if n == 0:
        return []
    return list(struct.unpack(f"<{n}f", blob))


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity. Returns 0.0 on mismatched dim — safer than raising."""
    if len(a) != len(b) or not a:
        return 0.0
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=False)) / (norm_a * norm_b)


def embed(
    text: str,
    *,
    base_url: str | None = None,
    model: str = DEFAULT_EMBED_MODEL,
    timeout: float = DEFAULT_TIMEOUT,
    normalize_output: bool = True,
) -> list[float] | None:
    """Return a normalized embedding for ``text``, or ``None`` on any failure.

    We never raise — the recall layer must keep working when the
    embedding server isn't running.
    """
    body = text.strip()
    if not body:
        return None

    url = f"{(base_url or _base_url()).rstrip('/')}/v1/embeddings"
    payload = {"model": model, "input": body}
    try:
        r = httpx.post(url, json=payload, headers=_auth_headers(), timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 — embedding is best-effort
        return None

    try:
        vec = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError):
        return None

    if not isinstance(vec, list) or not vec:
        return None
    try:
        vec = [float(x) for x in vec]
    except (TypeError, ValueError):
        return None

    return normalize(vec) if normalize_output else vec


def is_available(*, base_url: str | None = None, timeout: float = 1.0) -> bool:
    """Quick health probe. Fast on success, fast-ish on a closed port."""
    url = f"{(base_url or _base_url()).rstrip('/')}/health"
    try:
        r = httpx.get(url, timeout=timeout)
        return r.status_code == 200
    except Exception:  # noqa: BLE001
        return False
