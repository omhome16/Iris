"""Semantic recall cache: TTL'd in-memory hits, invalidated on mutation."""

from __future__ import annotations

import itertools
from datetime import date

import numpy as np
import pytest

import iris.memory.index as index_mod
from iris.config import settings
from iris.memory.index import MemoryIndex


class CountingLLM:
    """Counts embed calls; returns constant vectors (no network)."""

    def __init__(self) -> None:
        self.embeds = 0

    async def embed(self, texts):
        self.embeds += 1
        return [[0.1, 0.2]] * len(texts)

    async def embed_one(self, text):
        self.embeds += 1
        return [0.1, 0.2]


class _Vec:
    """Mimics pgvector's Vector (has .to_list()) for canned rows."""

    def __init__(self, values) -> None:
        self._values = np.asarray(values, dtype=float)

    def to_list(self):
        return self._values.tolist()


def make_row(content: str, origin: str = "owner") -> dict:
    return {
        "content": content,
        "path": "memory/MEMORY.md",
        "importance": 5.0,
        "origin": origin,
        "observed_at": date(2026, 8, 1),
        "evergreen": False,
        "embedding": _Vec([0.1, 0.2]),
        "chunk_index": 0,  # the search SQL always selects this (NOT NULL column)
        "vscore": 0.9,
        "fscore": 5.0,
    }


class RowIndex(MemoryIndex):
    """Search over canned rows: no Postgres pool needed."""

    def __init__(self, llm, rows):
        super().__init__("fake://dsn", llm)
        self._pool = object()  # type: ignore[assignment]
        self.rows = rows

    async def _search_rows(self, q_emb, query, origins, top_k):
        return [dict(r) for r in self.rows]

    async def _escalate_rows(self, q_emb, query, top_k):
        return [dict(r) for r in self.rows]

    async def connect(self) -> None:
        pass  # no DB in cache tests

    async def close(self) -> None:
        pass


async def test_repeat_query_hits_cache_and_normalizes_key():
    idx = RowIndex(CountingLLM(), [make_row("lease renews in September")])
    hits1 = await idx.search("when does the lease end")
    hits2 = await idx.search("  WHEN Does The LEASE END  ")
    assert hits1 and hits2
    assert hits2 == hits1  # same MemoryHit objects from the cache
    assert idx.llm.embeds == 1, "repeated query must not re-embed"


async def test_escalate_uses_its_own_cache_slot():
    idx = RowIndex(CountingLLM(), [make_row("we talked about X in July")])
    await idx.escalate("when did we discuss X")
    await idx.escalate("when did we discuss X")
    assert idx.llm.embeds == 1


async def test_cache_expires_after_ttl(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "recall_cache_ttl_seconds", 5.0)
    fake_clock = itertools.count(0, 10)  # 0, 10, 20, ...
    monkeypatch.setattr(index_mod.time, "monotonic", lambda: next(fake_clock))
    idx = RowIndex(CountingLLM(), [make_row("x")])
    await idx.search("q")
    await idx.search("q")  # t=20 vs store ts=10 → expired
    assert idx.llm.embeds == 2


async def test_cache_disabled_by_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "recall_cache_enabled", False)
    idx = RowIndex(CountingLLM(), [make_row("x")])
    await idx.search("q")
    await idx.search("q")
    assert idx.llm.embeds == 2


async def test_mutation_invalidates_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(index_mod, "register_vector", lambda conn: None)

    class FakeConn:
        async def execute(self, *args, **kwargs):
            pass

    class FakePool:
        def acquire(self):
            class _CM:
                async def __aenter__(self):
                    return FakeConn()

                async def __aexit__(self, *args):
                    return False

            return _CM()

    idx = RowIndex(CountingLLM(), [make_row("x")])
    idx._pool = FakePool()  # type: ignore[assignment]

    await idx.search("q")
    assert idx.llm.embeds == 1

    await idx.delete_file_chunks("memory/MEMORY.md")
    await idx.search("q")
    assert idx.llm.embeds == 2, "delete must invalidate recall cache"

    await idx.upsert_chunks([])  # empty batch: no embed, no mutation
    await idx.search("q")
    assert idx.llm.embeds == 2


async def test_different_queries_miss_each_other():
    idx = RowIndex(CountingLLM(), [make_row("x"), make_row("y")])
    await idx.search("alpha")
    await idx.search("beta")
    assert idx.llm.embeds == 2
