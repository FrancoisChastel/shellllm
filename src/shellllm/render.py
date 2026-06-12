"""Optional JS-rendering for ``fetch_url`` via a hosted/self-hosted service.

The default ``web.fetch_url`` flow fetches static HTML and reduces it
to text — fast, offline, and zero deps, but blind to anything an SPA
populates after first paint. This module adds an opt-in escape hatch:
if the user configures a Firecrawl-compatible endpoint, every
``fetch_url`` call tries the rendered path first and falls back to the
static fetcher on any failure.

The contract is Firecrawl's `/v1/scrape` shape because it's open,
documented, self-hostable, and already supported by a handful of
adjacent tools:

* hosted: https://api.firecrawl.dev
* self-hosted: https://github.com/mendableai/firecrawl

Anything else can be bridged with a tiny proxy that translates between
the upstream API and Firecrawl's shape.

Bring your own key
~~~~~~~~~~~~~~~~~~

::

    export SHELLLM_RENDER_URL="https://api.firecrawl.dev"
    export SHELLLM_RENDER_API_KEY="fc-..."

Without those two env vars the integration is inert and ``fetch_url``
behaves exactly as before.
"""

from __future__ import annotations

import os

import httpx

RENDER_URL_ENV = "SHELLLM_RENDER_URL"
RENDER_KEY_ENV = "SHELLLM_RENDER_API_KEY"
RENDER_TIMEOUT_ENV = "SHELLLM_RENDER_TIMEOUT"
DEFAULT_TIMEOUT = 30.0


def _config() -> tuple[str, str, float]:
    """Read env at call time so tests can monkeypatch cleanly."""
    base = os.environ.get(RENDER_URL_ENV, "").rstrip("/")
    key = os.environ.get(RENDER_KEY_ENV, "")
    timeout_raw = os.environ.get(RENDER_TIMEOUT_ENV, "")
    try:
        timeout = float(timeout_raw) if timeout_raw else DEFAULT_TIMEOUT
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    return base, key, timeout


def is_configured() -> bool:
    """True iff both URL and API key are set."""
    base, key, _ = _config()
    return bool(base and key)


def render_url(url: str) -> str | None:
    """POST the URL to the configured renderer, return rendered text or None.

    Returns ``None`` when the integration isn't configured, when the
    URL is empty, or when any error short-circuits the call. We never
    raise — ``fetch_url`` keeps working through its static-HTML
    fallback no matter what happens here.
    """
    base, key, timeout = _config()
    if not base or not key:
        return None
    target = url.strip()
    if not target:
        return None

    try:
        r = httpx.post(
            f"{base}/v1/scrape",
            json={"url": target, "formats": ["markdown"]},
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
    except Exception:  # noqa: BLE001 — rendering is best-effort
        return None

    return _extract_text(data)


def _extract_text(data: object) -> str | None:
    """Pull rendered text out of a Firecrawl-shaped response.

    The wire format has shifted across releases. We check the common
    fields, prefer markdown, and tolerate older "html only" payloads.
    """
    if not isinstance(data, dict):
        return None

    payload = data.get("data") if isinstance(data.get("data"), dict) else data
    if not isinstance(payload, dict):
        return None

    for key in ("markdown", "content", "text", "html"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
