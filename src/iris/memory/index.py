"""Memory index — Postgres + pgvector + FTS.

A derived view over the Markdown files. Schema:

    memory_chunks(id, path, chunk_index, content, origin, importance,
                  supersedes, trigger_phrases, observed_at, evergreen,
                  embedding)

Retrieval = hybrid fusion (vector cosine + FTS rank) × recency decay
(30-day half-life for dated episodic content; curated files are evergreen)
× importance multiplier, then MMR diversity. The default lane is fully
deterministic — no model call at query time (the Generative-Agents result:
score importance at write time, not query time).
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from iris.config import settings
from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin, Provenance

log = logging.getLogger("iris.memory.index")

_SCHEMA = f"""
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS memory_chunks (
    id              BIGSERIAL PRIMARY KEY,
    path            TEXT        NOT NULL,
    chunk_index     INT         NOT NULL,
    content         TEXT        NOT NULL,
    origin          TEXT        NOT NULL,
    importance      REAL        NOT NULL DEFAULT 0,
    supersedes      TEXT        NOT NULL DEFAULT '',
    trigger_phrases TEXT[]      NOT NULL DEFAULT '{{}}',
    observed_at     DATE        NOT NULL DEFAULT CURRENT_DATE,
    evergreen       BOOLEAN     NOT NULL DEFAULT FALSE,
    embedding       vector({settings.embedding_dim}),
    UNIQUE (path, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_memory_hnsw ON memory_chunks
    USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS idx_memory_fts ON memory_chunks
    USING gin (to_tsvector('english', content));
CREATE INDEX IF NOT EXISTS idx_memory_path ON memory_chunks (path);
"""


@dataclass(slots=True)
class MemoryHit:
    content: str
    path: str
    score: float
    importance: float
    origin: Origin
    observed_at: date
    evergreen: bool
    lane: str = "default"  # "default" | "escalate"


@dataclass(slots=True)
class ChunkRecord:
    path: str
    chunk_index: int
    content: str
    provenance: Provenance
    importance: float = 0.0
    trigger_phrases: list[str] = field(default_factory=list)
    evergreen: bool = False


def recency_weight(observed: date, *, today: date | None = None, half_life_days: int = 30) -> float:
    """Exponential decay: a dated note from one half-life ago scores 0.5."""
    days = max(0.0, ((today or date.today()) - observed).days)
    return math.exp(-math.log(2) * days / half_life_days)


class MemoryIndex:
    def __init__(self, dsn: str, llm: LLMClient) -> None:
        self.dsn = dsn
        self.llm = llm
        self._pool: asyncpg.Pool | None = None
        self._cache: dict[tuple, tuple[float, list[MemoryHit]]] = {}

    # ── recall cache (TTL, in-memory; cleared on any mutation) ───────────
    @staticmethod
    def _cache_key(*, query: str, top_k: int, mrr_top_k: int, origins, ablation) -> tuple:
        return (
            query.casefold().strip(),
            top_k,
            mrr_top_k,
            tuple(sorted(origins)),
            tuple(sorted(ablation)),
        )

    def _cached(self, key: tuple) -> list[MemoryHit] | None:
        if not settings.recall_cache_enabled:
            return None
        entry = self._cache.get(key)
        if entry is None:
            return None
        ts, hits = entry
        if time.monotonic() - ts > settings.recall_cache_ttl_seconds:
            self._cache.pop(key, None)
            return None
        return hits

    def _store(self, key: tuple, hits: list[MemoryHit]) -> None:
        if settings.recall_cache_enabled:
            self._cache[key] = (time.monotonic(), hits)

    def clear_cache(self) -> None:
        """Invalidate recall results after any index mutation."""
        self._cache.clear()

    async def connect(self) -> None:
        if self._pool is None:
            dsn = self.dsn.replace("postgresql+psycopg://", "postgresql://")

            async def init_conn(conn: asyncpg.Connection) -> None:
                await conn.execute(_SCHEMA)
                await register_vector(conn)

            self._pool = await asyncpg.create_pool(
                dsn, min_size=1, max_size=5, init=init_conn
            )
            await self._ensure_embedding_dim()

    async def _ensure_embedding_dim(self) -> None:
        """The schema bakes `embedding_dim` into the column at CREATE TABLE.
        If the model was switched (different vector size), the column type no
        longer matches and every insert fails. The index is a derived view
        over the Markdown files — it is rebuildable — so a mismatched table
        is dropped and recreated; the boot-time `reindex_all` repopulates it."""
        async with self._pool.acquire() as conn:
            typmod = await conn.fetchval(
                """
                SELECT atttypmod
                FROM pg_attribute
                WHERE attrelid = 'memory_chunks'::regclass
                  AND attname = 'embedding'
                """
            )
        # pgvector stores the vector length in atttypmod itself (1536 for a
        # vector(1536) column; -1 = no fixed length). Reading it as
        # `atttypmod - 4` mis-decoded 1536 as 1532 and dropped the table on
        # every connect.
        dim = typmod if (typmod is not None and typmod > 0) else None
        if dim is None or dim == settings.embedding_dim:
            return
        log.warning(
            "embedding column dim %s != configured %s — dropping and recreating "
            "memory_chunks (index is rebuildable from files)",
            dim,
            settings.embedding_dim,
        )
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DROP TABLE IF EXISTS memory_chunks")
                await conn.execute(_SCHEMA)
                await register_vector(conn)
        self.clear_cache()

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── write path ────────────────────────────────────────────────────────
    async def upsert_chunks(self, records: list[ChunkRecord]) -> None:
        if not records:
            return
        embeddings = await self.llm.embed([r.content for r in records])
        self.clear_cache()
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            for record, emb in zip(records, embeddings):
                await conn.execute(
                    """
                    INSERT INTO memory_chunks
                        (path, chunk_index, content, origin, importance,
                         supersedes, trigger_phrases, observed_at, evergreen, embedding)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                    ON CONFLICT (path, chunk_index)
                    DO UPDATE SET content=EXCLUDED.content, origin=EXCLUDED.origin,
                        importance=EXCLUDED.importance, supersedes=EXCLUDED.supersedes,
                        trigger_phrases=EXCLUDED.trigger_phrases,
                        observed_at=EXCLUDED.observed_at, evergreen=EXCLUDED.evergreen,
                        embedding=EXCLUDED.embedding
                    """,
                    record.path,
                    record.chunk_index,
                    record.content,
                    record.provenance.origin.value,
                    record.importance,
                    record.provenance.supersedes,
                    record.trigger_phrases,
                    record.provenance.observed_date,
                    record.evergreen,
                    emb,
                )

    async def delete_file_chunks(self, path: str) -> None:
        self.clear_cache()
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM memory_chunks WHERE path = $1", path)

    async def replace_file_chunks(self, path: str, records: list[ChunkRecord]) -> None:
        """Atomically replace one file's chunks: DELETE + INSERT in a single
        transaction. A crash mid-reindex used to leave the file half-indexed
        (old chunks for stale content deleted, new ones not yet written)."""
        if not records:
            await self.delete_file_chunks(path)
            return
        embeddings = await self.llm.embed([r.content for r in records])
        self.clear_cache()
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            async with conn.transaction():
                await conn.execute("DELETE FROM memory_chunks WHERE path = $1", path)
                for record, emb in zip(records, embeddings):
                    await conn.execute(
                        """
                        INSERT INTO memory_chunks
                            (path, chunk_index, content, origin, importance,
                             supersedes, trigger_phrases, observed_at, evergreen, embedding)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                        """,
                        record.path,
                        record.chunk_index,
                        record.content,
                        record.provenance.origin.value,
                        record.importance,
                        record.provenance.supersedes,
                        record.trigger_phrases,
                        record.provenance.observed_date,
                        record.evergreen,
                        emb,
                    )

    # ── recall (default lane: deterministic, no model call) ───────────────
    async def _search_rows(self, q_emb, query: str, origins: list[str], top_k: int) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            return await conn.fetch(
                """
                SELECT content, path, importance, origin, observed_at, evergreen, embedding,
                       (1 - (embedding <=> $1::vector)) AS vscore,
                       ts_rank(to_tsvector('english', content), plainto_tsquery('english', $2)) AS fscore
                FROM memory_chunks
                WHERE origin = ANY($3)
                ORDER BY vscore DESC
                LIMIT $4
                """,
                q_emb,
                query,
                origins,
                top_k * 4,
            )

    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        mrr_top_k: int = 5,
        require_origin: set[Origin] | None = None,
        ablation: set[str] | None = None,
    ) -> list[MemoryHit]:
        """Ablation knobs (eval-lab only, default = full pipeline):
        {"vector_only", "no_decay", "no_importance", "no_mmr"}."""
        ablation = ablation or set()
        origins = [o.value for o in (require_origin or {o for o in Origin})]
        key = self._cache_key(
            query=query, top_k=top_k, mrr_top_k=mrr_top_k, origins=origins, ablation=ablation
        )
        cached = self._cached(key)
        if cached is not None:
            return cached

        q_emb = await self.llm.embed_one(query)
        rows = await self._search_rows(q_emb, query, origins, top_k)

        hits: list[MemoryHit] = []
        pairs: list[tuple[MemoryHit, np.ndarray]] = []
        for row in rows:
            vscore = float(row["vscore"])
            fscore = float(row["fscore"]) / 10.0  # normalize FTS rank
            if "vector_only" in ablation:
                score = vscore
            else:
                hybrid = 0.6 * vscore + 0.4 * min(fscore, 1.0)
                decay = 1.0 if row["evergreen"] else recency_weight(row["observed_at"])
                if "no_decay" in ablation:
                    decay = 1.0
                importance = 1.0 + (float(row["importance"]) / 10.0)  # 1..2 multiplier
                if "no_importance" in ablation:
                    importance = 1.0
                score = hybrid * decay * importance
            hit = MemoryHit(
                content=row["content"],
                path=row["path"],
                score=score,
                importance=float(row["importance"]),
                origin=Origin(row["origin"]),
                observed_at=row["observed_at"],
                evergreen=row["evergreen"],
            )
            pairs.append((hit, np.asarray(row["embedding"].to_list(), dtype=float)))

        pairs.sort(key=lambda p: p[0].score, reverse=True)
        if "no_mmr" in ablation:
            hits = [hit for hit, _ in pairs[: mrr_top_k or top_k]]
        else:
            hits = self._mmr(pairs, top_k=mrr_top_k or top_k)
        self._store(key, hits)
        return hits

    async def _escalate_rows(self, q_emb, query: str, top_k: int) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            return await conn.fetch(
                """
                SELECT content, path, importance, origin, observed_at, evergreen, embedding,
                       (1 - (embedding <=> $1::vector)) AS vscore,
                       ts_rank(to_tsvector('english', content), plainto_tsquery('english', $2)) AS fscore
                FROM memory_chunks
                WHERE path ~ '^memory/[0-9]{4}-[0-9]{2}-[0-9]{2}\\.md$'
                ORDER BY vscore DESC
                LIMIT $3
                """,
                q_emb,
                query,
                top_k * 6,
            )

    async def escalate(self, query: str, *, top_k: int = 5, mrr_top_k: int = 5) -> list[MemoryHit]:
        """Escalation lane — direct scan of daily notes with decay disabled.

        The default lane is a *precision* device: recency decay deliberately
        demotes old episodic facts. Temporal/multi-hop questions ("when did we
        talk about X?", "what happened last month?") are exactly the case the
        default lane hides, so this lane trades precision tuning away: daily
        notes only, flat decay, hybrid FTS+vector ranking, MMR diversity.
        """
        key = self._cache_key(
            query=query, top_k=top_k, mrr_top_k=mrr_top_k, origins=["escalate"], ablation=()
        )
        cached = self._cached(key)
        if cached is not None:
            return cached

        q_emb = await self.llm.embed_one(query)
        rows = await self._escalate_rows(q_emb, query, top_k)

        pairs: list[tuple[MemoryHit, np.ndarray]] = []
        for row in rows:
            vscore = float(row["vscore"])
            fscore = float(row["fscore"]) / 10.0  # normalize FTS rank
            hybrid = 0.6 * vscore + 0.4 * min(fscore, 1.0)
            importance = 1.0 + (float(row["importance"]) / 10.0)  # 1..2 multiplier
            hit = MemoryHit(
                content=row["content"],
                path=row["path"],
                score=hybrid * importance,
                importance=float(row["importance"]),
                origin=Origin(row["origin"]),
                observed_at=row["observed_at"],
                evergreen=row["evergreen"],
                lane="escalate",
            )
            pairs.append((hit, np.asarray(row["embedding"].to_list(), dtype=float)))

        pairs.sort(key=lambda p: p[0].score, reverse=True)
        hits = self._mmr(pairs, top_k=mrr_top_k or top_k)
        self._store(key, hits)
        return hits

    @staticmethod
    def _mmr(pairs: list[tuple[MemoryHit, np.ndarray]], *, top_k: int, lam: float = 0.7) -> list[MemoryHit]:
        """Maximal Marginal Relevance: relevance minus redundancy against
        already-selected candidates (cosine similarity). Deterministic, local,
        no model calls — this is the MMR-diversity step from the recall lane."""
        if not pairs:
            return []
        selected: list[tuple[MemoryHit, np.ndarray]] = []
        pool = pairs[:]
        # Embeddings are stored as-is; embeddings from different providers are
        # not guaranteed unit-length, and dot product is only a cosine when
        # both vectors are normalized. Normalize defensively.
        pool = [(hit, emb / (np.linalg.norm(emb) or 1.0)) for hit, emb in pool]
        while pool and len(selected) < top_k:
            best: tuple[MemoryHit, np.ndarray] | None = None
            best_val = -1.0
            best_idx = -1
            for i, (hit, emb) in enumerate(pool):
                rel = hit.score
                if selected:
                    redundancy = max(float(np.dot(emb, s[1])) for s in selected)
                else:
                    redundancy = 0.0
                value = lam * rel - (1 - lam) * redundancy
                if value > best_val:
                    best_val, best_idx, best = value, i, (hit, emb)
            if best is None:
                break
            selected.append(best)
            pool.pop(best_idx)
        return [hit for hit, _ in selected]

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        self.clear_cache()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM memory_chunks WHERE path = $1 AND chunk_index = $2",
                path,
                chunk_index,
            )

    async def stats(self) -> dict:
        async with self._pool.acquire() as conn:
            total = await conn.fetchval("SELECT count(*) FROM memory_chunks")
            by_origin = dict(
                await conn.fetch("SELECT origin, count(*) FROM memory_chunks GROUP BY origin")
            )
            return {"total_chunks": total, "by_origin": by_origin}

    async def list_chunks(self) -> list[dict]:
        """All chunks (no embeddings) — for forgetting reports and dashboard."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT path, chunk_index, content, origin, importance,
                       supersedes, trigger_phrases, observed_at, evergreen
                FROM memory_chunks
                ORDER BY observed_at DESC
                """
            )
            return [dict(r) for r in rows]

    async def nearest(self, text: str, *, top_k: int = 3) -> list[dict]:
        """Raw cosine neighbors (no scoring) — for dedupe checks."""
        emb = await self.llm.embed_one(text)
        async with self._pool.acquire() as conn:
            await register_vector(conn)
            rows = await conn.fetch(
                """
                SELECT path, content, (1 - (embedding <=> $1::vector)) AS cos
                FROM memory_chunks
                ORDER BY embedding <=> $1::vector
                LIMIT $2
                """,
                emb,
                top_k,
            )
            return [{"path": r["path"], "content": r["content"], "cos": float(r["cos"])} for r in rows]