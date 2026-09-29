"""SQLite memory backend: the zero-service default.

Offline and file-backed (tmp_path), so these are the tests that can actually
run on a fresh clone — which is the whole point of the backend. They pin the
contracts the pgvector backend already had (both recall lanes, decay,
importance, MMR, atomic file replacement) plus the one thing only this backend
has: per-query degradation to keyword-only recall when no embedder is available.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest

from iris_ai.config import settings
from iris_ai.memory.index import ChunkRecord
from iris_ai.memory.provenance import Origin, Provenance
from iris_ai.memory.sqlite_index import SqliteIndex, _fts_match


class FakeLLM:
    """Embedder stub. `mapping` decides the vectors, `fail` simulates no key."""

    def __init__(self, mapping: dict[str, list[float]] | None = None, *, fail: bool = False) -> None:
        self.mapping = mapping or {}
        self.fail = fail
        self.calls = 0

    async def embed(self, texts, *, timeout: float = 60.0) -> list[list[float]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("no embedding provider configured")
        return [self.mapping.get(t, [0.0, 0.0]) for t in texts]

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


def rec(
    path: str,
    index: int,
    content: str,
    *,
    origin: Origin = Origin.OWNER,
    importance: float = 0.0,
    days_ago: int = 0,
    evergreen: bool = False,
) -> ChunkRecord:
    observed = datetime.now() - timedelta(days=days_ago)
    return ChunkRecord(
        path=path,
        chunk_index=index,
        content=content,
        provenance=Provenance(origin=origin, observed_at=observed),
        importance=importance,
        evergreen=evergreen,
    )


async def make_index(tmp_path, llm=None, **kw) -> SqliteIndex:
    index = SqliteIndex(tmp_path / "memory.db", llm=llm, **kw)
    await index.connect()
    return index


# ── lifecycle ────────────────────────────────────────────────────────────────
async def test_connect_creates_the_file_and_is_idempotent(tmp_path):
    index = await make_index(tmp_path)
    assert (tmp_path / "memory.db").exists()
    await index.connect()  # second connect must not raise or wipe
    assert (await index.stats())["total_chunks"] == 0
    await index.close()


async def test_search_before_connect_is_a_clear_error(tmp_path):
    from iris_ai.memory.index import MemoryUnavailable

    index = SqliteIndex(tmp_path / "memory.db", llm=None)
    with pytest.raises(MemoryUnavailable, match="not connected"):
        await index.search("anything")


# ── write path ───────────────────────────────────────────────────────────────
async def test_upsert_then_keyword_recall(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks([rec("memory/MEMORY.md", 0, "Omar plays the cello every Sunday")])
    hits = await index.search("cello", top_k=5)
    assert [h.path for h in hits] == ["memory/MEMORY.md"]
    assert hits[0].chunk_index == 0
    assert hits[0].origin is Origin.OWNER
    await index.close()


async def test_replace_file_chunks_swaps_content_and_keywords(tmp_path):
    index = await make_index(tmp_path)
    await index.replace_file_chunks("a.md", [rec("a.md", 0, "the retired thing")])
    await index.replace_file_chunks("a.md", [rec("a.md", 0, "the current thing")])

    assert await index.search("retired", top_k=5) == []
    hits = await index.search("current", top_k=5)
    assert [h.content for h in hits] == ["the current thing"]
    assert (await index.stats())["total_chunks"] == 1
    await index.close()


async def test_replace_with_no_records_deletes_the_file(tmp_path):
    index = await make_index(tmp_path)
    await index.replace_file_chunks("a.md", [rec("a.md", 0, "gone soon")])
    await index.replace_file_chunks("a.md", [])
    assert (await index.stats())["total_chunks"] == 0
    assert await index.search("gone", top_k=5) == []
    await index.close()


async def test_forget_entry_removes_one_chunk_only(tmp_path):
    index = await make_index(tmp_path)
    await index.replace_file_chunks(
        "a.md", [rec("a.md", 0, "keep this"), rec("a.md", 1, "forget this")]
    )
    await index.forget_entry("a.md", 1)
    chunks = await index.list_chunks()
    assert [c["chunk_index"] for c in chunks] == [0]
    assert await index.search("forget", top_k=5) == []
    await index.close()


async def test_upsert_updates_the_fts_row_too(tmp_path):
    """A stale FTS entry would keep matching content that no longer exists."""
    index = await make_index(tmp_path)
    await index.upsert_chunks([rec("a.md", 0, "the old wording")])
    await index.upsert_chunks([rec("a.md", 0, "the new wording")])
    assert await index.search("old", top_k=5) == []
    assert [h.content for h in await index.search("new", top_k=5)] == ["the new wording"]
    await index.close()


# ── recall policy (shared with the pgvector backend) ─────────────────────────
async def test_recency_decay_demotes_old_episodic_content(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks(
        [
            rec("memory/old.md", 0, "alpha note", days_ago=120),
            rec("memory/new.md", 0, "alpha note", days_ago=0),
        ]
    )
    hits = await index.search("alpha", top_k=5)
    assert [h.path for h in hits] == ["memory/new.md", "memory/old.md"]
    assert hits[0].decay > hits[1].decay
    await index.close()


async def test_evergreen_content_does_not_decay(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks([rec("memory/USER.md", 0, "beta fact", days_ago=900, evergreen=True)])
    hits = await index.search("beta", top_k=5)
    assert hits[0].decay == 1.0
    await index.close()


async def test_importance_multiplies_the_score(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks(
        [
            rec("a.md", 0, "gamma fact", importance=0.0),
            rec("b.md", 0, "gamma fact", importance=10.0),
        ]
    )
    hits = await index.search("gamma", top_k=5)
    assert [h.path for h in hits] == ["b.md", "a.md"]
    assert hits[0].imp_mult > hits[1].imp_mult
    await index.close()


async def test_origin_filter_is_respected(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks(
        [
            rec("owner.md", 0, "delta fact", origin=Origin.OWNER),
            rec("web.md", 0, "delta fact", origin=Origin.UNTRUSTED),
        ]
    )
    hits = await index.search("delta", top_k=5, require_origin={Origin.OWNER})
    assert [h.path for h in hits] == ["owner.md"]
    await index.close()


async def test_memory_off_ablation_returns_nothing(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks([rec("a.md", 0, "epsilon fact")])
    assert await index.search("epsilon", top_k=5, ablation={"memory_off"}) == []
    await index.close()


async def test_a_keyword_miss_returns_nothing_rather_than_padding(tmp_path):
    """The documented cost of the keyword-only lane, pinned as intended
    behaviour: with no embedder, a query that shares no token with any memory
    has no match. It must not be padded with unrelated recent notes, because a
    zero-score hit in the prompt is context pollution dressed as recall — the
    fix for this gap is an embedding key, which is what `iris init` recommends."""
    index = await make_index(tmp_path)
    await index.upsert_chunks([rec("memory/MEMORY.md", 0, "unrelated wording here")])
    assert await index.search("zzz no such token", top_k=5) == []
    await index.close()


async def test_escalate_sees_only_dated_daily_notes(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks(
        [
            rec("memory/2026-09-01.md", 0, "zeta conversation"),
            rec("memory/MEMORY.md", 0, "zeta curated fact"),
        ]
    )
    hits = await index.escalate("zeta", top_k=5)
    assert [h.path for h in hits] == ["memory/2026-09-01.md"]
    assert hits[0].lane == "escalate"
    assert hits[0].decay == 1.0  # the lane exists to defeat decay
    await index.close()


# ── vectors on: exact cosine, no keyword overlap required ────────────────────
async def test_vectors_find_a_semantic_match_with_no_keyword_overlap(tmp_path):
    llm = FakeLLM({"the cat sat": [1.0, 0.0], "feline": [1.0, 0.0], "taxes": [0.0, 1.0]})
    index = await make_index(tmp_path, llm=llm)
    await index.upsert_chunks(
        [
            rec("cat.md", 0, "the cat sat"),
            rec("tax.md", 0, "taxes"),
        ]
    )
    stats = await index.stats()
    assert stats["vectors"] is True and stats["embedded_chunks"] == 2

    hits = await index.search("feline", top_k=5)
    assert next(h.path for h in hits) == "cat.md"
    await index.close()


async def test_nearest_returns_cosine_neighbours(tmp_path):
    llm = FakeLLM({"a": [1.0, 0.0], "a too": [1.0, 0.0], "b": [0.0, 1.0]})
    index = await make_index(tmp_path, llm=llm)
    await index.upsert_chunks([rec("x.md", 0, "a"), rec("y.md", 0, "a too"), rec("z.md", 0, "b")])
    neighbours = await index.nearest("a", top_k=2)
    assert [n["path"] for n in neighbours] == ["x.md", "y.md"]
    assert neighbours[0]["cos"] == pytest.approx(1.0)
    await index.close()


# ── vectors off: degradation is per-query, reported, and recoverable ────────
async def test_no_embedder_degrades_to_keyword_recall(tmp_path):
    index = await make_index(tmp_path, llm=FakeLLM(fail=True))
    await index.upsert_chunks([rec("a.md", 0, "eta keyword")])
    # The write path could not embed, and the read path says so...
    stats = await index.stats()
    assert stats["vectors"] is False
    # ...but keyword recall still answers.
    hits = await index.search("eta", top_k=5)
    assert [h.path for h in hits] == ["a.md"]
    await index.close()


async def test_nearest_is_empty_rather_than_an_error_without_vectors(tmp_path):
    index = await make_index(tmp_path, llm=FakeLLM(fail=True))
    await index.upsert_chunks([rec("a.md", 0, "theta")])
    assert await index.nearest("theta") == []
    await index.close()


async def test_an_absent_embedder_is_not_called_on_every_query(tmp_path):
    """A missing key must not mean a failed provider call per turn."""
    llm = FakeLLM(fail=True)
    index = await make_index(tmp_path, llm=llm)
    await index.upsert_chunks([rec("a.md", 0, "iota")])
    before = llm.calls
    await index.search("iota", top_k=5)
    await index.search("iota two", top_k=5)
    assert llm.calls == before  # suppressed by the retry window
    await index.close()


async def test_vector_only_ablation_is_empty_when_vectors_are_off(tmp_path):
    index = await make_index(tmp_path, llm=FakeLLM(fail=True))
    await index.upsert_chunks([rec("a.md", 0, "kappa")])
    assert await index.search("kappa", top_k=5, ablation={"vector_only"}) == []
    await index.close()


# ── the query parser must not be able to break SQL ───────────────────────────
@pytest.mark.parametrize(
    "query",
    ['OR " unbalanced', "NEAR(*^) - '", "a" * 500, ""],
)
def test_fts_match_is_safe_and_quoted(query):
    match = _fts_match(query)
    if match is None:
        return
    # Every token is a quoted phrase: no FTS5 operator survives as syntax.
    assert match.count('"') % 2 == 0
    for token in match.split(" "):
        assert token.startswith('"') and token.endswith('"')


async def test_a_query_full_of_operators_does_not_raise(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks([rec("a.md", 0, "lambda fact")])
    # FTS5's own operators, as a user might type them: must return, not raise.
    await index.search('OR NEAR(" - * ^', top_k=5)
    await index.close()


# ── stats shape ──────────────────────────────────────────────────────────────
async def test_stats_reports_the_backend_location_and_origins(tmp_path):
    index = await make_index(tmp_path)
    await index.upsert_chunks(
        [
            rec("a.md", 0, "one", origin=Origin.OWNER),
            rec("b.md", 0, "two", origin=Origin.AGENT),
        ]
    )
    stats = await index.stats()
    assert stats["backend"] == "sqlite"
    assert stats["location"] == str(tmp_path / "memory.db")
    assert stats["by_origin"] == {"owner": 1, "agent": 1}
    await index.close()


async def test_embedding_blob_round_trips_exactly(tmp_path):
    """float32 storage must not lose the vector it was given."""
    vector = [0.5, -0.25, 0.125, 1.0]
    llm = FakeLLM({"v": vector})
    index = await make_index(tmp_path, llm=llm)
    await index.upsert_chunks([rec("v.md", 0, "v")])
    hits = await index.search("v", top_k=1)
    assert hits
    embedding = np.asarray(vector, dtype=np.float32)
    assert _cosine_of_stored(index, embedding) == pytest.approx(1.0, abs=1e-6)
    await index.close()


def _cosine_of_stored(index: SqliteIndex, expected: np.ndarray) -> float:
    raw = index._conn.execute("SELECT embedding FROM memory_chunks").fetchone()[0]
    stored = np.frombuffer(raw, dtype=np.float32)
    return float(np.dot(stored, expected) / (np.linalg.norm(stored) * np.linalg.norm(expected)))


async def test_tmp_path_independence(tmp_path, monkeypatch):
    """Two backends on different files do not see each other's memories."""
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "a.db"))
    first = await make_index(tmp_path / "one")
    second = await make_index(tmp_path / "two")
    await first.upsert_chunks([rec("only-in-first.md", 0, "mu unique")])
    assert await second.search("mu", top_k=5) == []
    await first.close()
    await second.close()
