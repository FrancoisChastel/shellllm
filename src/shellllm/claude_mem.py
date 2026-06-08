"""Optional integration with claude-mem's server-beta REST API.

Claude-mem stores observations (short narratives capturing what
happened) and exposes a "context" endpoint that returns observations
relevant to a query, pre-joined for prompt injection. We use both:

* on the first turn of a fresh shellllm session we **read** context
  for the user's question and inject it as an extra system message,
  giving the local model cross-session memory it otherwise lacks;
* after each turn we **write** an observation summarizing what the
  user asked and what they got back, so future sessions benefit;
* facts saved through ``? --remember`` are mirrored as observations
  (with ``kind="user-fact"``) — the local JSONL stays the source of
  truth for offline use, claude-mem just gets a copy.

This module is strictly opt-in: nothing fires unless the three
``CLAUDE_MEM_SERVER_BETA_*`` env vars are set. The user can also pin
``SHELLLM_CLAUDE_MEM=0`` to force-disable, or pass ``--no-mem`` for a
one-off skip. Failures never propagate — we'd rather drop one
observation than crash a terminal prompt.

Why server-beta instead of the worker?
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
claude-mem's local "worker" mode runs inside Claude Code's plugin
loader and has no documented external write API. The "server-beta"
mode is a hosted REST surface (``/v1/memories``, ``/v1/context``) that
shellllm can talk to from anywhere with just a bearer token — that's
the only stable integration point for a process running outside Claude
Code.
"""

from __future__ import annotations

import os
import sys
import threading
from typing import Any

import httpx

BASE_URL_ENV = "CLAUDE_MEM_SERVER_BETA_URL"
API_KEY_ENV = "CLAUDE_MEM_SERVER_BETA_API_KEY"
PROJECT_ID_ENV = "CLAUDE_MEM_SERVER_BETA_PROJECT_ID"
ENABLE_ENV = "SHELLLM_CLAUDE_MEM"

# Aggressive: we'd rather drop a write than block the user's prompt.
DEFAULT_WRITE_TIMEOUT = 3.0
DEFAULT_READ_TIMEOUT = 4.0

_DIM = "\x1b[2m"
_CYAN = "\x1b[36m"
_RESET = "\x1b[0m"

_HINT_SHOWN_KEY = "_SHELLLM_CLAUDE_MEM_HINTED"


def _hint_once(text: str) -> None:
    """Print a one-line stderr hint, but only the first time per process."""
    if os.environ.get(_HINT_SHOWN_KEY):
        return
    os.environ[_HINT_SHOWN_KEY] = "1"
    sys.stderr.write(f"{_DIM}{_CYAN}↻ {text}{_RESET}\n")
    sys.stderr.flush()


def _truthy(value: str) -> bool:
    return value.strip().lower() not in ("", "0", "false", "no", "off")


class ClaudeMemAdapter:
    """Thin REST client around claude-mem's server-beta endpoints.

    Stateless aside from configuration: every call constructs its own
    HTTP request so the adapter is safe to share across threads.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        project_id: str | None = None,
        enabled_override: bool | None = None,
        write_timeout: float = DEFAULT_WRITE_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self.base_url = (
            base_url if base_url is not None else os.environ.get(BASE_URL_ENV, "")
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        self.project_id = (
            project_id if project_id is not None else os.environ.get(PROJECT_ID_ENV, "")
        )
        self._enabled_override = enabled_override
        self.write_timeout = write_timeout
        self.read_timeout = read_timeout

    # ── State -------------------------------------------------------------

    @property
    def configured(self) -> bool:
        """All three required pieces of config are present."""
        return bool(self.base_url and self.api_key and self.project_id)

    @property
    def enabled(self) -> bool:
        """Considered "on" if configured AND the user hasn't opted out."""
        if self._enabled_override is False:
            return False
        if self._enabled_override is True:
            return self.configured
        env = os.environ.get(ENABLE_ENV)
        if env is not None and not _truthy(env):
            return False
        return self.configured

    # ── Writes ------------------------------------------------------------

    def record_observation_async(
        self,
        content: str,
        *,
        kind: str = "shellllm-turn",
        metadata: dict[str, Any] | None = None,
    ) -> threading.Thread | None:
        """Fire-and-forget. Returns the thread for tests; ignore in production.

        We avoid blocking the terminal prompt on a network round-trip:
        the model's answer is already on screen by the time this fires.
        """
        if not self.enabled:
            return None
        text = content.strip()
        if not text:
            return None

        payload: dict[str, Any] = {
            "projectId": self.project_id,
            "kind": kind,
            "type": kind,
            "narrative": text,
        }
        if metadata:
            payload["metadata"] = metadata

        def _go() -> None:
            try:
                self._post("/v1/memories", payload, timeout=self.write_timeout)
            except Exception:  # noqa: BLE001 — by design: never crash the user
                return

        thread = threading.Thread(target=_go, daemon=True, name="shellllm-mem-write")
        thread.start()
        return thread

    # ── Reads -------------------------------------------------------------

    def query_context(self, query: str, *, limit: int = 5) -> str | None:
        """Return a pre-joined context string for prompt injection, or None.

        ``None`` when disabled, when the query is empty, or when the
        call fails. We do not raise — the caller carries on without
        prior context.
        """
        if not self.enabled:
            return None
        q = query.strip()
        if not q:
            return None

        try:
            data = self._post(
                "/v1/context",
                {"projectId": self.project_id, "query": q, "limit": limit},
                timeout=self.read_timeout,
            )
        except Exception:  # noqa: BLE001
            return None

        # /v1/context shape has shifted across versions; be tolerant.
        for key in ("context", "narrative", "text"):
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, str) and value.strip():
                return value.strip()

        # Fallback: stitch together raw observation narratives if the
        # endpoint returned a list without a pre-joined string.
        if isinstance(data, dict):
            obs = data.get("observations") or data.get("results")
            if isinstance(obs, list):
                lines = []
                for entry in obs:
                    if isinstance(entry, dict):
                        narrative = (
                            entry.get("narrative") or entry.get("content") or entry.get("text")
                        )
                        if isinstance(narrative, str) and narrative.strip():
                            lines.append(f"- {narrative.strip()}")
                if lines:
                    return "\n".join(lines)

        return None

    # ── HTTP --------------------------------------------------------------

    def _post(self, path: str, body: dict[str, Any], *, timeout: float) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        r = httpx.post(url, json=body, headers=headers, timeout=timeout)
        r.raise_for_status()
        if not r.content:
            return {}
        try:
            data = r.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}


def render_context_block(context_text: str) -> str:
    """Wrap a context string in a clearly-tagged system-message block."""
    body = context_text.strip()
    if not body:
        return ""
    return "<claude-mem-context>\n" + body + "\n</claude-mem-context>"


def hint_on_first_use(adapter: ClaudeMemAdapter) -> None:
    """One-line hint so the user knows claude-mem is engaged this session."""
    if adapter.enabled:
        _hint_once("claude-mem on (injecting context, recording observations)")
