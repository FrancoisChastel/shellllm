"""Tests for the archive store (FTS5 + optional embeddings)."""

from __future__ import annotations

import math

import pytest

from shellllm.archive import (
    Archive,
    _build_fts_query,
    _rrf_fuse,
    flatten_transcript,
    render_hits_block,
)
from shellllm.embed import normalize


@pytest.fixture
def archive(tmp_path):
    return Archive(path=tmp_path / "archive.db")


def _user_assistant(q: str, a: str) -> list[dict]:
    return [
        {"role": "user", "content": q},
        {"role": "assistant", "content": a},
    ]


def test_flatten_skips_tool_messages():
    messages = [
        {"role": "user", "content": "find foo"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "web_search", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "x", "content": "GIANT NOISY TOOL OUTPUT"},
        {"role": "assistant", "content": "found foo at /bar"},
    ]
    out = flatten_transcript(messages)
    assert "Q: find foo" in out
    assert "A: found foo at /bar" in out
    assert "GIANT NOISY" not in out


def test_flatten_preserves_summary_marker():
    messages = [
        {"role": "system", "content": "<summary-so-far>\nearlier: rg basics\n</summary-so-far>"},
        {"role": "user", "content": "and then?"},
        {"role": "assistant", "content": "we moved on to fd"},
    ]
    out = flatten_transcript(messages)
    assert "<summary-so-far>" in out
    assert "fd" in out


def test_ingest_then_search_finds_term(archive):
    archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="/tmp",
        last_date="2026-06-08",
        turn_count=2,
        messages=_user_assistant("how do I use ripgrep", "rg pattern path"),
    )
    hits = archive.search("ripgrep")
    assert len(hits) == 1
    assert hits[0].cmd == "ask"
    assert hits[0].last_pwd == "/tmp"
    assert "ripgrep" in hits[0].content.lower()
    assert hits[0].fts_rank is not None


def test_search_empty_archive_returns_empty(archive):
    assert archive.search("anything") == []


def test_empty_transcript_is_not_ingested(archive):
    out = archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=0,
        messages=[],
    )
    assert out is None
    assert archive.count() == 0


def test_ingest_via_embed_fn_stores_vector(archive):
    def fake_embed(text):
        return normalize([1.0, 0.5, 0.0])

    rid = archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=1,
        messages=_user_assistant("foo", "bar"),
        embed_fn=fake_embed,
    )
    assert rid is not None
    # Find via cosine sim against same direction → should retrieve it.
    hits = archive.search("foo", query_embedding=normalize([1.0, 0.5, 0.0]))
    assert len(hits) == 1
    assert hits[0].cosine_score is not None
    assert hits[0].cosine_score > 0.99


def test_failing_embed_fn_does_not_raise(archive):
    def broken(text):
        raise RuntimeError("embed server down")

    rid = archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=1,
        messages=_user_assistant("a", "b"),
        embed_fn=broken,
    )
    assert rid is not None
    assert archive.count() == 1


def test_search_mismatched_embedding_dim_is_tolerated(archive):
    """Different embed models produce different dims; recall must still
    return BM25 hits when the query vector can't match stored ones."""

    archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=1,
        messages=_user_assistant("greppy thing", "use ripgrep"),
        embedding=normalize([1.0, 0.0, 0.0]),  # 3-dim stored
    )
    hits = archive.search("greppy", query_embedding=normalize([1.0, 0.0]))  # 2-dim query
    # BM25 hit still works; cosine just skipped that row.
    assert len(hits) == 1
    assert hits[0].fts_rank is not None


def test_cmd_filter_restricts_results(archive):
    archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=1,
        messages=_user_assistant("ripgrep usage", "rg ..."),
    )
    archive.ingest_session(
        cmd="search",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=1,
        messages=_user_assistant("ripgrep latest version", "v14.x"),
    )
    ask_hits = archive.search("ripgrep", cmd_filter="ask")
    search_hits = archive.search("ripgrep", cmd_filter="search")
    assert len(ask_hits) == 1
    assert ask_hits[0].cmd == "ask"
    assert len(search_hits) == 1
    assert search_hits[0].cmd == "search"


def test_build_fts_query_drops_punctuation():
    assert _build_fts_query("hello, world!") == "hello OR world"


def test_build_fts_query_handles_quotes_and_specials():
    # No FTS5 operators in output → safe to pass to MATCH.
    out = _build_fts_query('what does "rg" do?')
    assert '"' not in out
    assert "*" not in out
    assert "rg" in out.lower()


def test_build_fts_query_empty_returns_empty():
    assert _build_fts_query("") == ""
    assert _build_fts_query("   ") == ""


def test_rrf_fuses_two_rankings():
    a = [(1, 0.0), (2, 0.0), (3, 0.0)]
    b = [(3, 0.0), (1, 0.0)]
    out = _rrf_fuse(a, b, k=60)
    # Items in both rank higher than items in only one.
    ids_only = [hit_id for hit_id, _ in out]
    assert ids_only[0] in {1, 3}
    assert 2 in ids_only


def test_render_hits_block_empty_returns_empty():
    assert render_hits_block([]) == ""


def test_render_hits_block_tags_each_hit(archive):
    archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="/proj",
        last_date="2026-06-08",
        turn_count=1,
        messages=_user_assistant("about ripgrep", "use rg"),
    )
    hits = archive.search("ripgrep")
    block = render_hits_block(hits)
    assert block.startswith("<shellllm-recall>")
    assert block.endswith("</shellllm-recall>")
    assert "ripgrep" in block.lower()


def test_search_results_have_fused_score(archive):
    archive.ingest_session(
        cmd="ask",
        terminal_id="t1",
        created_at=1.0,
        last_used=2.0,
        last_pwd="",
        last_date="",
        turn_count=1,
        messages=_user_assistant("about ripgrep", "use rg"),
    )
    hits = archive.search("ripgrep")
    assert hits[0].fused_score > 0.0


def test_normalize_unit_norm():
    # Sanity check the helper from embed used as fixture input.
    out = normalize([3.0, 4.0])
    assert math.isclose(math.sqrt(sum(x * x for x in out)), 1.0, abs_tol=1e-6)
