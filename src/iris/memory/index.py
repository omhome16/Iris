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

import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import asyncpg
import numpy as np
from pgvector.asyncpg import register_vector

from iris.config import settings
from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin, Provenance

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

    async def connect(self) -> None:
        if self._pool is None:
            dsn = self.dsn.replace("postgresql+psycopg://", "postgresql://")

            async def init_conn(conn: asyncpg.Connection) -> None:
                await conn.execute(_SCHEMA)
                await register_vector(conn)

            self._pool = await asyncpg.create_pool(
                dsn, min_size=1, max_size=5, init=init_conn
            )

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None

    # ── write path ────────────────────────────────────────────────────────
    async def upsert_chunks(self, records: list[ChunkRecord]) -> None:
        if not records:
            return
        embeddings = await self.llm.embed([r.content for r in records])
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
        async with self._pool.acquire() as conn:
            await conn.execute("DELETE FROM memory_chunks WHERE path = $1", path)

    # ── recall (default lane: deterministic, no model call) ───────────────
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
        q_emb = await self.llm.embed_one(query)
        origins = [o.value for o in (require_origin or {o for o in Origin})]

        async with self._pool.acquire() as conn:
            await register_vector(conn)
            rows = await conn.fetch(
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
            return [hit for hit, _ in pairs[: mrr_top_k or top_k]]
        return self._mmr(pairs, top_k=mrr_top_k or top_k)

    @staticmethod
    def _mmr(pairs: list[tuple[MemoryHit, np.ndarray]], *, top_k: int, lam: float = 0.7) -> list[MemoryHit]:
        """Maximal Marginal Relevance: relevance minus redundancy against
        already-selected candidates (cosine similarity). Deterministic, local,
        no model calls — this is the MMR-diversity step from the recall lane."""
        if not pairs:
            return []
        selected: list[tuple[MemoryHit, np.ndarray]] = []
        pool = pairs[:]
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