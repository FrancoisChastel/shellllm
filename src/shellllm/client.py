"""Thin HTTP client for a local llama-server (OpenAI-compatible API).

Two entry points: ``chat`` for one-shot completions (used by ``,`` because
its JSON-schema response needs to be parsed whole) and ``chat_stream`` for
incremental tokens (used by ``?`` so the user sees output as it generates).
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("SHELLLM_BASE_URL", "http://127.0.0.1:8080")
DEFAULT_MODEL = os.environ.get("SHELLLM_MODEL", "local")
DEFAULT_TIMEOUT = float(os.environ.get("SHELLLM_TIMEOUT", "120"))

# Env name read lazily so tests can monkeypatch + the same key powers
# both chat and (optionally) embeddings without an import-time race.
API_KEY_ENV = "SHELLLM_API_KEY"


def _auth_headers() -> dict[str, str]:
    """Build request headers with optional Bearer auth.

    The local ``llama-server`` doesn't need auth, so the bearer token
    is opt-in via ``SHELLLM_API_KEY``. Setting it lets you point
    ``SHELLLM_BASE_URL`` at any OpenAI-compatible endpoint (OpenAI,
    OpenRouter, Groq, Together, Mistral, …) and have the chat path
    "just work".
    """
    headers = {"Content-Type": "application/json"}
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


class LlamaServerError(RuntimeError):
    pass


def chat(
    messages: list[dict[str, Any]],
    *,
    response_format: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
    enable_thinking: bool = False,
    base_url: str = DEFAULT_BASE_URL,
) -> dict[str, Any]:
    """Send a chat completion request. Returns the first choice's message dict.

    Qwen3-family models default to extended reasoning, which routes tokens
    into a separate ``reasoning_content`` field and starves the visible
    ``content`` of budget. We disable it by default — flip
    ``enable_thinking=True`` if you actually want it for a given call.

    Raises LlamaServerError on connection failure or non-2xx status, with a
    message the caller can print directly.
    """
    payload: dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if response_format is not None:
        payload["response_format"] = response_format
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    try:
        r = httpx.post(
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=_auth_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        raise LlamaServerError(
            f"can't reach llama-server at {base_url}\n"
            "  what to do: run `??` to start it "
            "(or `export SHELLLM_AUTOSTART=1` to start on demand)"
        ) from exc
    except httpx.ReadTimeout as exc:
        raise LlamaServerError(f"llama-server timed out after {DEFAULT_TIMEOUT}s") from exc

    if r.status_code != 200:
        raise LlamaServerError(f"llama-server {r.status_code}: {r.text[:300]}")

    data = r.json()
    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError) as exc:
        raise LlamaServerError(f"unexpected response shape: {data}") from exc


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1500,
    enable_thinking: bool = False,
    base_url: str = DEFAULT_BASE_URL,
) -> Iterator[dict[str, Any]]:
    """Stream a chat completion as a sequence of event dicts.

    Yields:
      {"type": "content", "text": "..."}        text token(s)
      {"type": "done", "finish_reason": str,
       "tool_calls": [...]}                     last event, with any
                                                accumulated tool calls in
                                                OpenAI format

    The streaming endpoint splits a single tool call across many deltas
    (id, name, then arguments piecewise). We re-assemble them here so the
    caller gets the same shape ``chat()`` would have returned.
    """
    payload: dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if tools is not None:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    pending: dict[int, dict[str, str]] = {}
    finish_reason: str | None = None
    timeout = httpx.Timeout(connect=10.0, read=DEFAULT_TIMEOUT, write=10.0, pool=10.0)

    try:
        with httpx.stream(
            "POST",
            f"{base_url}/v1/chat/completions",
            json=payload,
            headers=_auth_headers(),
            timeout=timeout,
        ) as r:
            if r.status_code != 200:
                body = b"".join(r.iter_bytes()).decode("utf-8", errors="replace")
                raise LlamaServerError(f"llama-server {r.status_code}: {body[:300]}")

            for line in r.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload_str = line[5:].strip()
                if payload_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload_str)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}

                text = delta.get("content")
                if text:
                    yield {"type": "content", "text": text}

                for tc in delta.get("tool_calls") or []:
                    idx = tc.get("index", 0)
                    slot = pending.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                    if tc.get("id"):
                        slot["id"] = tc["id"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]

                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
    except httpx.ConnectError as exc:
        raise LlamaServerError(
            f"can't reach llama-server at {base_url}\n"
            "  what to do: run `??` to start it "
            "(or `export SHELLLM_AUTOSTART=1` to start on demand)"
        ) from exc
    except httpx.ReadTimeout as exc:
        raise LlamaServerError("llama-server stream timed out") from exc

    tool_calls = [
        {
            "id": slot["id"],
            "type": "function",
            "function": {"name": slot["name"], "arguments": slot["arguments"]},
        }
        for _, slot in sorted(pending.items())
        if slot["name"]
    ]
    yield {"type": "done", "finish_reason": finish_reason, "tool_calls": tool_calls}
