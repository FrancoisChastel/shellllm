"""Tests for the conversation-compaction heuristic."""

from __future__ import annotations

from shellllm.compact import (
    CHARS_PER_TOKEN,
    KEEP_LAST_TURNS,
    build_summary_prompt,
    compact,
    estimate_tokens,
)


def _make_turn(user_text: str, asst_text: str) -> list[dict]:
    return [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": asst_text},
    ]


def test_estimate_tokens_counts_content():
    messages = [{"role": "user", "content": "a" * 350}]
    expected = int(350 / CHARS_PER_TOKEN)
    assert estimate_tokens(messages) == expected


def test_estimate_tokens_includes_tool_calls():
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "web_search", "arguments": '{"query":"foo"}'}}],
        }
    ]
    assert estimate_tokens(messages) > 0


def test_compact_noop_below_threshold():
    msgs = [{"role": "system", "content": "rules"}] + _make_turn("hi", "hello")
    out, report = compact(
        msgs,
        summarize=lambda _: "should not be called",
        ctx_tokens=100_000,
    )
    assert not report.triggered
    assert out == msgs


def test_compact_runs_when_over_trigger():
    big_user = "u " * 5000  # ~10000 chars → ~2800 tokens
    big_asst = "a " * 5000
    msgs: list[dict] = [{"role": "system", "content": "rules"}]
    # 10 turns, each big. Total ~28k tokens.
    for i in range(10):
        msgs.extend(_make_turn(f"q{i}: {big_user}", f"a{i}: {big_asst}"))

    summary_calls: list[int] = []

    def fake_summary(slice_):
        summary_calls.append(len(slice_))
        return "earlier we talked about q0..q5"

    out, report = compact(
        msgs,
        summarize=fake_summary,
        ctx_tokens=32_768,
    )

    assert report.triggered
    assert summary_calls, "summarizer should have been called once"

    # System prompt preserved.
    assert out[0]["role"] == "system"
    assert out[0]["content"] == "rules"

    # Followed by the summary-so-far marker.
    assert "<summary-so-far>" in out[1]["content"]

    # Last KEEP_LAST_TURNS user/assistant pairs preserved verbatim.
    user_msgs_in_out = [m for m in out if m.get("role") == "user"]
    assert len(user_msgs_in_out) == KEEP_LAST_TURNS
    # The very last user message should be the last one we appended.
    assert user_msgs_in_out[-1]["content"].startswith("q9:")

    # And total token estimate dropped.
    assert report.after_tokens < report.before_tokens


def test_compact_does_not_split_a_turn():
    """A turn that includes assistant tool_calls + role=tool replies must
    survive together — the OpenAI schema requires tool replies to follow
    their initiating tool_calls message."""
    big = "x" * 10000
    msgs: list[dict] = [{"role": "system", "content": "rules"}]
    for i in range(8):
        msgs.append({"role": "user", "content": f"q{i} {big}"})
        msgs.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": f"call-{i}",
                        "function": {"name": "web_search", "arguments": '{"q":"x"}'},
                    }
                ],
            }
        )
        msgs.append({"role": "tool", "tool_call_id": f"call-{i}", "content": big})
        msgs.append({"role": "assistant", "content": f"answer {i}"})

    out, report = compact(
        msgs,
        summarize=lambda _: "summary",
        ctx_tokens=16_000,
    )
    assert report.triggered

    # Walk the tail: every tool_calls message must have a tool reply after it.
    role_seq = [m.get("role") for m in out]
    for i, role in enumerate(role_seq):
        if role == "assistant" and out[i].get("tool_calls"):
            assert role_seq[i + 1] == "tool"


def test_summary_prompt_flattens_transcript():
    middle = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    prompt = build_summary_prompt(middle)
    assert prompt[0]["role"] == "system"
    assert prompt[1]["role"] == "user"
    assert "[user] hi" in prompt[1]["content"]
    assert "[assistant] hello" in prompt[1]["content"]


def test_compact_returns_messages_unchanged_when_no_middle():
    """If there aren't more than KEEP_LAST_TURNS turns, there's nothing
    older to summarize even past the trigger — better to bail than to
    silently produce a wonky list."""
    big = "x" * 50000
    msgs = [{"role": "system", "content": "rules"}, *_make_turn(big, big)]
    out, report = compact(
        msgs,
        summarize=lambda _: "summary",
        ctx_tokens=20_000,
    )
    assert not report.triggered
    assert out == msgs
