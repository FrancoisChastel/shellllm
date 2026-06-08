"""Conversation compaction — keep the context window from blowing up.

Strategy: ``ConversationSummaryBufferMemory``, minimal flavour.

When the rough token count of the message list crosses
``COMPACT_TRIGGER_FRACTION`` of the configured context window, we:

1. Keep the system prompt(s) at the head.
2. Keep the last ``KEEP_LAST_TURNS`` user/assistant turns verbatim
   (so the immediate thread of conversation is intact).
3. Ask the local model to summarize everything between, then drop those
   middle messages and replace them with one synthetic
   ``role=system`` message tagged ``<summary-so-far>``.

That gives us bounded growth at the cost of one extra round-trip when
the threshold is hit. We don't pull in LangChain / LlamaIndex / mem0 —
all of them carry too much dependency surface for what is fundamentally
a tiny token-budget heuristic on top of the same model we already use.
The contract lives behind a single ``compact()`` function so swapping in
an external library later is local-only.

Tool-call interleavings
~~~~~~~~~~~~~~~~~~~~~~~
The agent loop emits ``role=assistant`` messages with ``tool_calls`` and
matching ``role=tool`` replies. The OpenAI chat schema requires every
``tool_calls`` message to be followed by its matching ``tool`` results
or the next request 400s. The compactor therefore splits on full
"turns" — a turn always starts with ``role=user`` and ends just before
the next ``role=user`` — and never drops half of one.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# 32k is the project default (see SHELLLM_CTX in zsh/shellllm.zsh).
DEFAULT_CTX_TOKENS = int(os.environ.get("SHELLLM_CTX", "32768"))

# Fire compaction at 80% to leave headroom for the response + tools.
COMPACT_TRIGGER_FRACTION = 0.80

# Below this fraction we bail and ask the user to --reset. Past this
# point, further summarization would lose the recent thread.
COMPACT_HARD_CAP_FRACTION = 0.90

# How many user/assistant turn pairs at the end we keep verbatim.
KEEP_LAST_TURNS = 4

# Token-budget for the summary itself. Large enough to keep the gist,
# small enough that the savings are meaningful.
SUMMARY_MAX_TOKENS = 600

# Char-to-token estimate. ~3.5 for English+code is the standard rough cut.
CHARS_PER_TOKEN = 3.5


@dataclass(frozen=True)
class CompactionResult:
    """Reported back so the CLI can log what happened."""

    triggered: bool
    before_tokens: int
    after_tokens: int
    summarized_messages: int
    over_hard_cap: bool


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """Rough total token count. Counts content + serialized tool_calls."""
    chars = 0
    for m in messages:
        c = m.get("content")
        if isinstance(c, str):
            chars += len(c)
        elif isinstance(c, list):
            for part in c:
                if isinstance(part, dict):
                    chars += len(str(part.get("text", "")))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            chars += len(str(fn.get("name", "")))
            chars += len(str(fn.get("arguments", "")))
    return int(chars / CHARS_PER_TOKEN)


# Callable that takes (messages_to_summarize) and returns the summary text.
# Injected so compact() doesn't depend on client.py directly — keeps the
# test path mockable and the cyclic-import risk at zero.
Summarizer = Callable[[list[dict[str, Any]]], str]


def compact(
    messages: list[dict[str, Any]],
    summarize: Summarizer,
    *,
    ctx_tokens: int = DEFAULT_CTX_TOKENS,
    keep_last_turns: int = KEEP_LAST_TURNS,
    trigger_fraction: float = COMPACT_TRIGGER_FRACTION,
    hard_cap_fraction: float = COMPACT_HARD_CAP_FRACTION,
) -> tuple[list[dict[str, Any]], CompactionResult]:
    """Possibly shrink ``messages``. Returns the (maybe new) list + a report.

    The list is returned even when no compaction happens, so callers can
    write `messages, _ = compact(...)` unconditionally.
    """
    before = estimate_tokens(messages)
    trigger = int(ctx_tokens * trigger_fraction)
    hard_cap = int(ctx_tokens * hard_cap_fraction)

    if before < trigger:
        return messages, CompactionResult(
            triggered=False,
            before_tokens=before,
            after_tokens=before,
            summarized_messages=0,
            over_hard_cap=False,
        )

    head, middle, tail = _partition(messages, keep_last_turns)
    if not middle:
        return messages, CompactionResult(
            triggered=False,
            before_tokens=before,
            after_tokens=before,
            summarized_messages=0,
            over_hard_cap=before > hard_cap,
        )

    summary_text = summarize(middle)
    summary_message: dict[str, Any] = {
        "role": "system",
        "content": "<summary-so-far>\n" + summary_text.strip() + "\n</summary-so-far>",
    }

    new_messages = head + [summary_message] + tail
    after = estimate_tokens(new_messages)

    return new_messages, CompactionResult(
        triggered=True,
        before_tokens=before,
        after_tokens=after,
        summarized_messages=len(middle),
        over_hard_cap=after > hard_cap,
    )


def _partition(
    messages: list[dict[str, Any]],
    keep_last_turns: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Split into (head=system msgs, middle=to-summarize, tail=recent turns).

    A turn starts at each ``role=user`` and runs up to (but not
    including) the next ``role=user``. We never split a turn — the
    OpenAI schema would 400 if we left a ``tool_calls`` message without
    its matching ``role=tool`` reply.
    """
    head: list[dict[str, Any]] = []
    body_start = 0
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            head.append(m)
            body_start = i + 1
        else:
            break

    body = messages[body_start:]
    user_indices = [i for i, m in enumerate(body) if m.get("role") == "user"]

    if len(user_indices) <= keep_last_turns:
        return head, [], body

    split_at = user_indices[-keep_last_turns]
    middle = body[:split_at]
    tail = body[split_at:]
    return head, middle, tail


SUMMARIZER_SYSTEM = (
    "You are summarizing an earlier portion of a conversation between a user "
    "and an assistant so the assistant can keep going without forgetting "
    "context. Write a short factual summary in 6-12 lines covering: what the "
    "user asked, what was discovered (key file paths, URLs, versions, "
    "command outputs), and any decisions made. Use bullet points. No "
    "preamble, no closing remarks. Skip pleasantries."
)


def build_summary_prompt(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a slice of messages into a single chat request for summarization.

    We flatten the slice into a transcript inside the user message rather
    than pass them as real chat turns — that way the summarizer sees the
    history as data, not as a continuation to extend.
    """
    transcript_lines: list[str] = []
    for m in messages:
        role = str(m.get("role", "unknown"))
        content = m.get("content") or ""
        if not isinstance(content, str):
            content = str(content)
        transcript_lines.append(f"[{role}] {content.strip()}")
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function") or {}
            transcript_lines.append(
                f"[{role} tool_call] {fn.get('name', '?')}({fn.get('arguments', '')})"
            )
    transcript = "\n".join(transcript_lines)

    return [
        {"role": "system", "content": SUMMARIZER_SYSTEM},
        {"role": "user", "content": f"Transcript to summarize:\n\n{transcript}"},
    ]
