"""Tests for the long-term fact store behind ``? --remember``."""

from __future__ import annotations

import pytest

from shellllm.memory import (
    MAX_FACT_CHARS,
    MAX_FACTS,
    MemoryStore,
    render_memory_block,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(tmp_path / "memory.jsonl")


def test_empty_store_loads_to_empty_list(store):
    assert store.load() == []


def test_add_persists_fact(store):
    store.add("the project uses Qwen3 locally")
    facts = store.load()
    assert len(facts) == 1
    assert facts[0].text == "the project uses Qwen3 locally"


def test_add_empty_text_is_rejected(store):
    with pytest.raises(ValueError):
        store.add("   ")


def test_add_truncates_oversize_input(store):
    big = "x" * (MAX_FACT_CHARS + 100)
    fact = store.add(big)
    assert len(fact.text) == MAX_FACT_CHARS
    assert fact.text.endswith("…")


def test_overflow_spills_to_archive(store):
    for i in range(MAX_FACTS + 3):
        store.add(f"fact {i}")
    remaining = store.load()
    assert len(remaining) == MAX_FACTS
    # Oldest 3 should be in archive.
    assert store.archive_path.exists()
    archive_lines = store.archive_path.read_text(encoding="utf-8").splitlines()
    assert len(archive_lines) == 3
    # And the remaining facts are the most recent ones.
    assert remaining[-1].text == f"fact {MAX_FACTS + 2}"


def test_forget_removes_by_one_based_index(store):
    store.add("a")
    store.add("b")
    store.add("c")
    removed = store.forget(2)
    assert removed is not None
    assert removed.text == "b"
    remaining = [f.text for f in store.load()]
    assert remaining == ["a", "c"]


def test_forget_out_of_range_returns_none(store):
    store.add("a")
    assert store.forget(5) is None
    assert store.forget(0) is None


def test_clear_returns_count_and_empties(store):
    store.add("a")
    store.add("b")
    assert store.clear() == 2
    assert store.load() == []


def test_render_block_returns_empty_for_no_facts():
    assert render_memory_block([]) == ""


def test_render_block_includes_each_fact(store):
    store.add("alpha")
    store.add("beta")
    block = render_memory_block(store.load())
    assert "<memory>" in block
    assert "</memory>" in block
    assert "- alpha" in block
    assert "- beta" in block


def test_corrupt_line_is_skipped_on_load(tmp_path):
    path = tmp_path / "memory.jsonl"
    path.write_text(
        '{"ts": 1, "text": "ok"}\n'
        "this is not json\n"
        '{"ts": 2, "text": ""}\n'  # empty text should be skipped
        '{"ts": 3, "text": "another"}\n',
        encoding="utf-8",
    )
    store = MemoryStore(path)
    facts = store.load()
    texts = [f.text for f in facts]
    assert texts == ["ok", "another"]
