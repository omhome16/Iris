import json
from pathlib import Path

from iris.memory.chunking import (
    chunk_text,
    contextualize_chunks,
    estimate_tokens,
    load_context_cache,
    store_context_cache,
)
from iris.memory.index import recency_weight


def test_estimate_tokens_counts_words():
    assert estimate_tokens("") == 0
    assert estimate_tokens("one two three") == 3


def test_chunk_small_text_stays_whole():
    chunks = chunk_text("hello world", chunk_tokens=400, overlap_tokens=80)
    assert chunks == ["hello world"]


def test_chunk_long_text_with_overlap():
    text = " ".join(f"word{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=20)
    assert len(chunks) > 10
    assert " ".join(chunks[0].split()) == " ".join(chunks[0].split())
    first_tokens = chunks[0].split()
    second_tokens = chunks[1].split()
    assert second_tokens[0] == first_tokens[-20]


class ContextLLM:
    def __init__(self, reply: str | None = None) -> None:
        self.calls = 0
        self.reply = reply

    async def complete(self, messages, **kwargs):
        self.calls += 1
        if self.reply is not None:
            return self.reply
        n = len(messages[1]["content"].split("[chunk "))
        return json.dumps(
            {"contexts": [{"chunk": i, "context": f"Context header for chunk {i}."} for i in range(n)]}
        )


def test_contextual_chunks_prepend_headers(tmp_path: Path):
    text = " ".join(f"word{i}" for i in range(250))
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=20)
    llm = ContextLLM()
    out = run_async(contextualize_chunks(chunks, llm=llm, cache_dir=tmp_path, text=text))
    assert out[0].startswith("Context header for chunk 0.")
    assert out[1].startswith("Context header for chunk 1.")
    assert chunks[0] in out[0]


def test_contextual_fallback_on_bad_json(tmp_path: Path):
    chunks = chunk_text(" ".join(f"word{i}" for i in range(250)), chunk_tokens=100, overlap_tokens=20)
    llm = ContextLLM(reply="not json at all")
    out = run_async(contextualize_chunks(chunks, llm=llm, cache_dir=tmp_path, text="x"))
    assert out == chunks


def test_contextual_disabled_never_calls_llm(tmp_path: Path):
    chunks = chunk_text("a b c d e f g h i j k l", chunk_tokens=6, overlap_tokens=2)
    llm = ContextLLM()
    out = run_async(
        contextualize_chunks(chunks, llm=llm, cache_dir=tmp_path, text="t", enabled=False)
    )
    assert out == chunks
    assert llm.calls == 0


def test_context_cache_hit_skips_llm(tmp_path: Path):
    text = " ".join(f"word{i}" for i in range(250))
    store_context_cache(tmp_path, text, {0: "cached ctx", 1: "cached ctx 2"})
    llm = ContextLLM()
    chunks = chunk_text(text, chunk_tokens=100, overlap_tokens=20)
    out = run_async(contextualize_chunks(chunks, llm=llm, cache_dir=tmp_path, text=text))
    assert llm.calls == 0
    assert out[0].startswith("cached ctx")
    assert out[1].startswith("cached ctx 2")


def test_context_cache_stale_hash_is_ignored(tmp_path: Path):
    text = " ".join(f"word{i}" for i in range(250))
    store_context_cache(tmp_path, text, {0: "cached ctx"})
    assert load_context_cache(tmp_path, text + " changed") is None


def test_context_headers_truncated_to_budget(tmp_path: Path, monkeypatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "context_header_tokens", 8)
    chunks = chunk_text(" ".join(f"word{i}" for i in range(250)), chunk_tokens=100, overlap_tokens=20)
    llm = ContextLLM(reply=json.dumps({"contexts": [{"chunk": 0, "context": "a b c d e f g h i j k l m n o p"}]}))
    out = run_async(contextualize_chunks(chunks, llm=llm, cache_dir=tmp_path, text="t"))
    header = out[0].split("\n\n")[0]
    assert len(header.split()) <= 8


def run_async(coro):
    import asyncio

    return asyncio.run(coro)


def test_recency_decay_half_life():
    from datetime import date, timedelta

    today = date(2026, 8, 15)
    fresh = recency_weight(today, today=today)
    assert fresh == 1.0
    one_hl = recency_weight(today - timedelta(days=30), today=today)
    assert abs(one_hl - 0.5) < 1e-9
    two_hl = recency_weight(today - timedelta(days=60), today=today)
    assert abs(two_hl - 0.25) < 1e-9
    future = recency_weight(today + timedelta(days=10), today=today)
    assert future == 1.0


def test_provenance_promotable_only_trusted():
    from iris.memory.provenance import Origin

    assert Origin.OWNER.promotable
    assert Origin.AGENT.promotable
    assert not Origin.UNTRUSTED.promotable
    assert not Origin.SYSTEM.promotable
