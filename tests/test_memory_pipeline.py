"""Integration test: full memory pipeline against live Postgres.

Uses deterministic fake embeddings so no API key is needed. Requires the
postgres container to be up (docker compose up -d postgres).
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest
from asyncpg.exceptions import InvalidCatalogNameError

from iris.memory.files import WorkspaceFiles
from iris.memory.index import ChunkRecord, MemoryIndex
from iris.memory.indexer import Reindexer
from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin, Provenance

DSN = os.getenv(
    "IRIS_TEST_POSTGRES_DSN",
    "postgresql+psycopg://iris:iris_dev_password@localhost:5433/iris_test",
)


class FakeLLM(LLMClient):
    """Deterministic hash-based embeddings; no external calls."""

    def __init__(self) -> None:
        self.embedding_dim = 1536

    async def embed(self, texts: list[str], *, timeout: float = 60.0) -> list[list[float]]:
        out = []
        for t in texts:
            vec = np.zeros(self.embedding_dim, dtype=float)
            for tok in t.split():
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                vec[h % self.embedding_dim] += 1.0
            norm = np.linalg.norm(vec) or 1.0
            out.append((vec / norm).tolist())
        return out

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


@pytest.fixture
async def env(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    files.append_daily("Went to the beach with Sam. Loved the weather.", day=date.today())
    files.append_daily(
        "Owner mentioned they prefer green tea over coffee.",
        day=date.today() - timedelta(days=40),
    )
    llm = FakeLLM()
    index = MemoryIndex(DSN, llm)
    try:
        await index.connect()
    except InvalidCatalogNameError as exc:
        # A missing database used to surface as five cryptic asyncpg errors.
        # These tests deliberately use their own database, so say how to make
        # one instead of leaving the reader to decode the traceback.
        pytest.fail(
            f"the memory-pipeline test database does not exist ({exc}). Create it with:\n"
            "  docker compose exec postgres psql -U iris -d iris -c 'CREATE DATABASE iris_test;'",
            pytrace=False,
        )
    except OSError as exc:
        pytest.fail(
            f"no Postgres is listening at the test DSN ({exc}). Start one with:\n"
            "  docker compose up -d postgres",
            pytrace=False,
        )
    yield files, index
    await index.close()


async def test_reindex_and_hybrid_search(env):
    files, index = env
    reindexer = Reindexer(files, index)
    count = await reindexer.reindex_all()
    assert count >= 2

    hits = await index.search("what tea does the owner like", top_k=5, mrr_top_k=3)
    assert hits, "expected at least one hit"
    assert hits[0].path.startswith("memory/")

    stats = await index.stats()
    assert stats["total_chunks"] >= 2
    assert stats["by_origin"].get("agent", 0) >= 2


async def test_recency_decay_ranks_fresh_higher(env):
    files, index = env
    reindexer = Reindexer(files, index)
    await reindexer.reindex_all()

    hits = await index.search("owner's day out with Sam", top_k=5, mrr_top_k=3)
    assert hits[0].path == f"memory/{date.today().isoformat()}.md"


async def test_upsert_and_forget(env):
    _files, index = env
    await index.upsert_chunks(
        [
            ChunkRecord(
                path="memory/test.md",
                chunk_index=0,
                content="Owner is learning Rust.",
                provenance=Provenance(origin=Origin.AGENT, source="memory/test.md"),
                importance=6.0,
            )
        ]
    )
    hits = await index.search("programming language the owner studies", top_k=5, mrr_top_k=3)
    assert any("Rust" in h.content for h in hits)

    await index.forget_entry("memory/test.md", 0)
    hits = await index.search("programming language the owner studies", top_k=5, mrr_top_k=3)
    assert not any("Rust" in h.content for h in hits)


async def test_evergreen_and_dated_origins(env):
    files, index = env
    files.write_curated(files.memory, "# MEMORY.md\n\n- [8] Owner's dog is named Milo\n")
    reindexer = Reindexer(files, index)
    await reindexer.reindex_all()

    hits = await index.search("Milo the dog", top_k=5, mrr_top_k=3)
    assert hits[0].path == "MEMORY.md"
    assert hits[0].evergreen is True
    assert hits[0].origin is Origin.OWNER


async def test_escalation_lane_finds_old_daily_facts(env):
    """The default lane's recency decay buries old episodic facts; the
    escalation lane scans daily notes with decay disabled and finds them."""
    files, index = env
    files.append_daily(
        "Owner's childhood cat was named Mochi.",
        day=date.today() - timedelta(days=400),
    )
    # fresh but irrelevant noise — default-lane bait (decay 1.0)
    files.append_daily("Bought cat food today.", day=date.today())
    reindexer = Reindexer(files, index)
    await reindexer.reindex_all()

    default = await index.search("what was the owner's childhood cat called", top_k=3, mrr_top_k=3)
    assert not any("Mochi" in h.content for h in default), (
        "400-day-old fact must be decayed out of the default lane"
    )

    esc = await index.escalate("what was the owner's childhood cat called", top_k=3, mrr_top_k=3)
    assert esc, "escalation lane must return hits"
    assert esc[0].lane == "escalate"
    assert any("Mochi" in h.content for h in esc), (
        "escalation lane must recover the old fact (no decay)"
    )
