"""SQLite + FTS5 memory index — the zero-service default.

The documented happy path used to require Docker and Postgres with the pgvector
extension. A personal assistant that wants a database daemon running before it
can remember your name is not a five-minute install, so the default backend is
one SQLite file beside the Markdown that it indexes. No daemon, no port, no
extension to build.

Retrieval deliberately mirrors `MemoryIndex` — same hybrid fusion, same recency
decay, same importance multiplier, same MMR diversity (`memory/scoring.py`) —
because that is *policy*, not storage. Only where the numbers come from differs:

- **keyword term** — FTS5 `bm25()`, normalized against the strongest match of
  the *same* query. FTS5's bm25 is unbounded and corpus-dependent (a one-document
  corpus scores ~1e-6, a large one scores in the tens), so an absolute scale like
  Postgres's `ts_rank` would make the term meaningless on small corpora. This is
  a real difference from the pgvector backend and it is recorded here rather
  than hidden: the two backends' keyword terms are comparable but not identical.
- **vector term** — exact cosine over the stored embeddings, computed in numpy.

Why exact cosine and not `sqlite-vec`'s ANN index: an exact scan is correct by
construction and testable offline, whereas a native extension we cannot install
in CI is a code path nobody can verify. The cost is O(n) per query over the
embedded rows; for a personal memory (10^4 chunks ≈ 60 MB of float32) that is
milliseconds. `nearest()` and `search()` both document the bound instead of
promising an index they do not have.

Degradation is **per query and recoverable**, never silent: if the embedder has
no key (or the provider is briefly down) the vector term is dropped and recall
runs keyword-only. That is a *worse* answer, not a broken one — and `stats()`
reports `vectors: false` together with the reason the last probe learned, so a
caller can tell "no embedding provider" apart from "nothing indexed yet" instead
of guessing. (`iris init` goes one better and probes the embedder itself, which
is what lets it name the fix rather than the symptom.)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from iris_ai import turnlog
from iris_ai.config import settings
from iris_ai.memory.index import (
    ChunkRecord,
    MemoryHit,
    MemoryUnavailable,
    recency_weight,
)
from iris_ai.memory.provenance import Origin
from iris_ai.memory.scoring import mmr_select

log = logging.getLogger("iris_ai.memory.sqlite")

_UNSET = object()


def _as_date(value: object) -> date:
    """SQLite stores observed_at as text. Callers subtract it from a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


#: How long a failed embed probe suppresses the vector term before retrying.
#: A transient provider blip must not disable semantic recall for the lifetime
#: of the process; a genuinely absent key just fails again and costs one call
#: every five minutes.
_VECTOR_RETRY_SECONDS = 300.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_chunks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    path            TEXT    NOT NULL,
    chunk_index     INTEGER NOT NULL,
    content         TEXT    NOT NULL,
    origin          TEXT    NOT NULL,
    importance      REAL    NOT NULL DEFAULT 0,
    supersedes      TEXT    NOT NULL DEFAULT '',
    trigger_phrases TEXT    NOT NULL DEFAULT '[]',
    observed_at     TEXT    NOT NULL,
    evergreen       INTEGER NOT NULL DEFAULT 0,
    embedding       BLOB,
    UNIQUE (path, chunk_index)
);
CREATE INDEX IF NOT EXISTS idx_memory_path ON memory_chunks (path);
CREATE INDEX IF NOT EXISTS idx_memory_origin ON memory_chunks (origin);

-- FTS5 is contentless-with-id: the row content lives in `memory_chunks` (the
-- source of truth for scoring) and this table exists only to answer MATCH and
-- produce bm25(). `chunk_id` carries memory_chunks.id so a hit can be joined
-- back; keeping the text out of both would save space at the cost of a join we
-- already do.
CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
    content,
    chunk_id UNINDEXED,
    tokenize = 'porter unicode61'
);

-- The escalation lane scans only dated daily notes (see `MemoryIndex.escalate`).
-- Postgres uses a regex; GLOB is SQLite's equivalent and its character classes
-- express "four digits - two digits - two digits" the same way.
"""

_DAILY_GLOB = "memory/[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9].md"

_TOKEN = re.compile(r"[A-Za-z0-9_]+")


def _fts_match(query: str) -> str | None:
    """Turn free text into a safe FTS5 MATCH expression.

    Every token is quoted, so FTS5 operators a user typed (`OR`, `*`, `NEAR`,
    `-`) are treated as words instead of syntax — a query must never be able to
    raise a SQL-level parse error, and `"` inside a token is stripped. Tokens
    are then AND-ed: `bm25` ranks documents matching more of the query higher,
    so OR would only widen the candidate pool with weak matches.
    """
    tokens = [t for t in _TOKEN.findall(query or "") if len(t) > 1]
    if not tokens:
        return None
    return " ".join('"' + t.replace('"', "") + '"' for t in tokens)


def _blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def _unblob(raw: bytes | None) -> np.ndarray | None:
    if not raw:
        return None
    return np.frombuffer(raw, dtype=np.float32)


def _cosine(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Cosine similarity, or 0.0 when either vector is missing/zero."""
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


class SqliteIndex:
    """Same surface as `MemoryIndex`, backed by one SQLite file."""

    def __init__(
        self,
        path: str | Path = "config/memory.db",
        llm: object | None = None,
        reranker: object | None = None,
        **_unused: Any,
    ) -> None:
        # `**_unused` swallows the uniform boot kwargs the engine hands every
        # backend (`dsn=…` among them), so choosing a backend is a settings
        # change rather than a different construction call.
        self.db_path = Path(path)
        self.dsn = str(self.db_path)  # the degraded-message contract names a location
        self.llm = llm
        self.reranker = reranker
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()
        self._cache: dict[tuple, tuple[float, list[MemoryHit]]] = {}
        self._vectors_reason: str | None = None
        self._vectors_retry_at = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────
    async def connect(self) -> None:
        await asyncio.to_thread(self._connect_sync)

    def _connect_sync(self) -> None:
        if self._conn is not None:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        # WAL so a read (a turn's recall) never blocks the write that banks the
        # turn's note; the file is a derived view and both are frequent.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn

    async def close(self) -> None:
        await asyncio.to_thread(self._close_sync)

    def _close_sync(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _require(self) -> sqlite3.Connection:
        if self._conn is None:
            raise MemoryUnavailable(
                f"memory backend not connected at {self.db_path}; call connect() first"
            )
        return self._conn

    # ── recall cache (same contract as MemoryIndex) ──────────────────────
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
        self._cache.clear()

    # ── write path ───────────────────────────────────────────────────────
    def _delete_path_sync(self, conn: sqlite3.Connection, path: str) -> None:
        """Remove a file's FTS rows, then its chunks, in one statement each."""
        conn.execute(
            "DELETE FROM memory_fts WHERE chunk_id IN "
            "(SELECT id FROM memory_chunks WHERE path = ?)",
            (path,),
        )
        conn.execute("DELETE FROM memory_chunks WHERE path = ?", (path,))

    def _insert_records_sync(
        self, conn: sqlite3.Connection, records: list[ChunkRecord], embeddings: list
    ) -> None:
        for record, emb in zip(records, embeddings, strict=False):
            cursor = conn.execute(
                """
                INSERT INTO memory_chunks
                    (path, chunk_index, content, origin, importance,
                     supersedes, trigger_phrases, observed_at, evergreen, embedding)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.path,
                    record.chunk_index,
                    record.content,
                    record.provenance.origin.value,
                    record.importance,
                    record.provenance.supersedes,
                    json.dumps(list(record.trigger_phrases)),
                    record.provenance.observed_date.isoformat(),
                    1 if record.evergreen else 0,
                    _blob(emb) if emb is not None else None,
                ),
            )
            chunk_id = cursor.lastrowid
            # The FTS row carries the same content; it is written here so a
            # chunk that lands in `memory_chunks` is never invisible to MATCH.
            conn.execute(
                "INSERT INTO memory_fts (content, chunk_id) VALUES (?, ?)",
                (record.content, chunk_id),
            )

    async def upsert_chunks(self, records: list[ChunkRecord]) -> None:
        if not records:
            return
        embeddings = await self._embed([r.content for r in records])

        def work() -> None:
            conn = self._require()
            with self._lock, conn:
                for record, emb in zip(records, embeddings, strict=False):
                    cursor = conn.execute(
                        """
                        INSERT INTO memory_chunks
                            (path, chunk_index, content, origin, importance,
                             supersedes, trigger_phrases, observed_at, evergreen, embedding)
                        VALUES (?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT (path, chunk_index) DO UPDATE SET
                            content=excluded.content, origin=excluded.origin,
                            importance=excluded.importance, supersedes=excluded.supersedes,
                            trigger_phrases=excluded.trigger_phrases,
                            observed_at=excluded.observed_at,
                            evergreen=excluded.evergreen, embedding=excluded.embedding
                        """,
                        (
                            record.path,
                            record.chunk_index,
                            record.content,
                            record.provenance.origin.value,
                            record.importance,
                            record.provenance.supersedes,
                            json.dumps(list(record.trigger_phrases)),
                            record.provenance.observed_date.isoformat(),
                            1 if record.evergreen else 0,
                            _blob(emb) if emb is not None else None,
                        ),
                    )
                    # An upsert reuses the row, so its FTS row must be replaced
                    # too: a stale FTS entry would keep matching content that no
                    # longer exists at that chunk.
                    conn.execute("DELETE FROM memory_fts WHERE chunk_id = ?", (cursor.lastrowid,))
                    conn.execute(
                        "INSERT INTO memory_fts (content, chunk_id) VALUES (?, ?)",
                        (record.content, cursor.lastrowid),
                    )

        self.clear_cache()
        await asyncio.to_thread(work)

    async def delete_file_chunks(self, path: str) -> None:
        def work() -> None:
            conn = self._require()
            with self._lock, conn:
                self._delete_path_sync(conn, path)

        self.clear_cache()
        await asyncio.to_thread(work)

    async def replace_file_chunks(self, path: str, records: list[ChunkRecord]) -> None:
        """Atomically replace one file's chunks (DELETE + INSERT in one txn).

        A crash mid-reindex must not leave a file half-indexed: the old chunks
        deleted and the new ones not yet written is exactly the state that makes
        recall quietly forget a file.
        """
        if not records:
            await self.delete_file_chunks(path)
            return
        embeddings = await self._embed([r.content for r in records])

        def work() -> None:
            conn = self._require()
            with self._lock, conn:
                self._delete_path_sync(conn, path)
                self._insert_records_sync(conn, records, embeddings)

        self.clear_cache()
        await asyncio.to_thread(work)

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        def work() -> None:
            conn = self._require()
            with self._lock, conn:
                conn.execute(
                    "DELETE FROM memory_fts WHERE chunk_id IN "
                    "(SELECT id FROM memory_chunks WHERE path = ? AND chunk_index = ?)",
                    (path, chunk_index),
                )
                conn.execute(
                    "DELETE FROM memory_chunks WHERE path = ? AND chunk_index = ?",
                    (path, chunk_index),
                )

        self.clear_cache()
        await asyncio.to_thread(work)

    # ── embeddings (optional by design) ──────────────────────────────────
    def _embedding_configured(self) -> bool:
        if self.llm is None:
            return False
        model = getattr(self.llm, "embedding_model", _UNSET)
        # A test double with no embedding_model attribute still embeds. A real
        # client with a blank model must not call the provider ("You passed model=").
        if model is not _UNSET and not str(model or "").strip():
            if self._vectors_reason is None:
                self._vectors_reason = "no embedding model configured"
                log.info("sqlite memory: vectors off, keyword recall only (no embedding model configured)")
            return False
        return True

    async def _embed(self, texts: list[str]) -> list[Any]:
        """Embed for the write path; `None` per text when vectors are off.

        Not an error: an owner with no embedding key still gets keyword recall
        and a durable index. The dimension is whatever the provider returns.
        """
        if self.llm is None or not self._embedding_configured() or not await self._vectors_usable():
            return [None] * len(texts)
        try:
            vectors = await self.llm.embed(texts)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the index must still be written
            self._note_vector_failure(exc)
            return [None] * len(texts)
        return [np.asarray(v, dtype=np.float32) for v in vectors]

    async def _query_vector(self, query: str) -> np.ndarray | None:
        if self.llm is None or not self._embedding_configured() or not await self._vectors_usable():
            return None
        try:
            vector = await self.llm.embed_one(query)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - degrade to keyword recall
            self._note_vector_failure(exc)
            return None
        return np.asarray(vector, dtype=np.float32)

    async def _vectors_usable(self) -> bool:
        if self._vectors_reason is None:
            return True
        return time.monotonic() >= self._vectors_retry_at

    def _note_vector_failure(self, exc: Exception) -> None:
        first = self._vectors_reason is None
        self._vectors_reason = f"{type(exc).__name__}: {exc}"
        self._vectors_retry_at = time.monotonic() + _VECTOR_RETRY_SECONDS
        if first:
            log.warning(
                "sqlite memory: vectors off, keyword recall only (%s); "
                "set an embedding-capable provider key to turn them on",
                self._vectors_reason,
            )

    # ── candidate gathering (blocking; always run in a thread) ───────────
    def _gather_sync(
        self,
        query: str,
        q_vec: np.ndarray | None,
        origins: list[str] | None,
        top_k: int,
        *,
        require_daily: bool,
    ) -> list[dict]:
        """Candidate rows, each carrying its raw vscore and normalized keyword score.

        `origins=None` means "every origin" — the escalation lane filters by path
        (dated daily notes only), not by provenance, in both backends. An empty
        list would build `origin IN ()`, which is a SQL syntax error, so the two
        cases are distinguished rather than collapsed.
        """
        conn = self._require()
        origin_clause = ""
        origin_args: list[str] = []
        if origins:
            origin_clause = f" AND c.origin IN ({','.join('?' for _ in origins)})"
            origin_args = list(origins)
        daily = f" AND c.path GLOB '{_DAILY_GLOB}'" if require_daily else ""
        limit = top_k * 4

        with self._lock:
            # 1. keyword pool — bm25 is negative, lower is better.
            fts_scores: dict[int, float] = {}
            match = _fts_match(query)
            if match:
                rows = conn.execute(
                    f"""
                    SELECT f.chunk_id AS id, bm25(memory_fts) AS bm
                    FROM memory_fts f
                    JOIN memory_chunks c ON c.id = f.chunk_id
                    WHERE memory_fts MATCH ?{origin_clause}{daily}
                    ORDER BY bm
                    LIMIT ?
                    """,
                    (match, *origin_args, limit),
                ).fetchall()
                fts_scores = {int(r[0]): float(r[1]) for r in rows}

            # 2. vector pool — exact cosine over every embedded row. This is the
            #    O(n) scan the docstring owns; the candidate limit keeps only the
            #    best, so the surviving work is proportional to top_k.
            vec_scores: dict[int, float] = {}
            if q_vec is not None:
                embedded = conn.execute(
                    f"""
                    SELECT c.id AS id, c.embedding AS embedding
                    FROM memory_chunks c
                    WHERE c.embedding IS NOT NULL{origin_clause}{daily}
                    """,
                    (*origin_args,),
                ).fetchall()
                scored = [
                    (int(r[0]), _cosine(q_vec, _unblob(r[1])))
                    for r in embedded
                    if _unblob(r[1]) is not None
                ]
                scored.sort(key=lambda item: item[1], reverse=True)
                vec_scores = dict(scored[:limit])

            # 3. No padding. An empty candidate set is a real answer: in
            #    keyword-only mode a query that shares no token with any memory
            #    genuinely has no match, and framing the most recent note as its
            #    "recall" would put unrelated context in the prompt with a zero
            #    score. The vector lane always has candidates (every text has a
            #    nearest neighbour) and needs no help either.
            ids = list(dict.fromkeys([*fts_scores, *vec_scores]))
            if not ids:
                return []

            id_placeholders = ",".join("?" for _ in ids)
            full = conn.execute(
                f"""
                SELECT id, path, chunk_index, content, origin, importance,
                       observed_at, evergreen, embedding
                FROM memory_chunks WHERE id IN ({id_placeholders})
                """,
                tuple(ids),
            ).fetchall()

        max_fts = max((abs(bm) for bm in fts_scores.values()), default=0.0)
        out: list[dict] = []
        for row in full:
            cid = int(row[0])
            out.append(
                {
                    "id": cid,
                    "path": row[1],
                    "chunk_index": int(row[2]),
                    "content": row[3],
                    "origin": row[4],
                    "importance": float(row[5]),
                    "observed_at": date.fromisoformat(row[6]),
                    "evergreen": bool(row[7]),
                    "embedding": _unblob(row[8]),
                    "vscore": vec_scores.get(cid, 0.0),
                    "fspan": (abs(fts_scores[cid]) / max_fts) if cid in fts_scores and max_fts else 0.0,
                }
            )
        return out

    # ── scoring (mirrors MemoryIndex; policy lives in one place) ─────────
    def _build_hits(
        self,
        rows: list[dict],
        *,
        vector_only: bool,
        no_decay: bool,
        no_importance: bool,
        vectors: bool,
    ) -> list[MemoryHit]:
        hits: list[MemoryHit] = []
        for row in rows:
            if vector_only and not vectors:
                continue
            vscore = float(row["vscore"])
            fspan = float(row["fspan"])
            if vector_only:
                relevance = vscore
            elif vectors:
                # Same 0.6/0.4 fusion as the pgvector backend.
                relevance = 0.6 * vscore + 0.4 * min(fspan, 1.0)
            else:
                # No geometry to blend with: the keyword term *is* the relevance
                # term. Decay and importance still apply below.
                relevance = fspan
            decay = 1.0 if row["evergreen"] else recency_weight(row["observed_at"])
            if no_decay:
                decay = 1.0
            imp_mult = 1.0 + (row["importance"] / 10.0)
            if no_importance:
                imp_mult = 1.0
            hits.append(
                MemoryHit(
                    content=row["content"],
                    path=row["path"],
                    score=relevance * decay * imp_mult,
                    importance=row["importance"],
                    origin=Origin(row["origin"]),
                    observed_at=row["observed_at"],
                    evergreen=row["evergreen"],
                    chunk_index=row["chunk_index"],
                    relevance=relevance,
                    decay=decay,
                    imp_mult=imp_mult,
                )
            )
        return hits

    async def _rank(
        self,
        query: str,
        rows: list[dict],
        *,
        ablation: set[str],
        escalate: bool,
        vectors: bool,
    ) -> list[tuple[MemoryHit, np.ndarray]]:
        """Score candidates and return (hit, embedding) pairs, best first.

        The pairs are returned rather than bare hits because MMR needs the
        vertices: diversity is a property of the *set* of candidates, so it
        cannot be computed downstream from scores alone.
        """
        hits = self._build_hits(
            rows,
            vector_only="vector_only" in ablation,
            # The escalation lane deliberately has no recency decay: it exists to
            # answer "when did we talk about X?", which decay is designed to hide.
            no_decay="no_decay" in ablation or escalate,
            no_importance="no_importance" in ablation,
            vectors=vectors,
        )
        pairs: list[tuple[MemoryHit, np.ndarray]] = []
        for hit, row in zip(hits, rows, strict=False):
            embedding = row["embedding"]
            if escalate:
                hit.lane = "escalate"
            # A zero vector is honest here: it means "this memory has no
            # geometry, treat it as maximally distinct", which is exactly how
            # an FTS-only store should behave under MMR.
            pairs.append((hit, embedding if embedding is not None else np.zeros(1, dtype=np.float32)))
        pairs.sort(key=lambda p: p[0].score, reverse=True)
        if "vector_only" not in ablation and "no_rerank" not in ablation:
            await self._rerank(query, pairs)
        return pairs

    async def _rerank(self, query: str, pairs: list[tuple[MemoryHit, np.ndarray]]) -> None:
        """Replace the relevance term with a JEV judgment, in place.

        Identical contract to `MemoryIndex._rerank`: best-effort, and the
        deterministic policy multipliers (decay, importance) are preserved
        because JEV answers *how relevant*, never *what may be forgotten*.
        """
        if self.reranker is None or not getattr(self.reranker, "enabled", False):
            return
        try:
            with turnlog.stage("rerank"):
                nouls = await self.reranker.relevance(  # type: ignore[attr-defined]
                    query, [hit.content for hit, _ in pairs]
                )
        except Exception as exc:  # noqa: BLE001 - rerank must never break recall
            log.warning("jev rerank failed, keeping deterministic order: %s", exc)
            return
        if not nouls:
            return
        blend = float(getattr(self.reranker, "blend", 0.15))
        for (hit, _emb), noul_score in zip(pairs, nouls, strict=False):
            hit.relevance = (1.0 - blend) * float(noul_score) + blend * hit.relevance
            hit.score = hit.relevance * hit.decay * hit.imp_mult
        pairs.sort(key=lambda p: p[0].score, reverse=True)

    # ── recall lanes ─────────────────────────────────────────────────────
    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        mrr_top_k: int = 5,
        require_origin: set[Any] | None = None,
        ablation: set[str] | None = None,
    ) -> list[MemoryHit]:
        """Default lane — see `MemoryIndex.search` for the ablation vocabulary."""
        ablation = ablation or set()
        if "memory_off" in ablation:
            return []
        origins = [o.value for o in (require_origin or set(Origin))]
        key = self._cache_key(
            query=query, top_k=top_k, mrr_top_k=mrr_top_k, origins=origins, ablation=ablation
        )
        cached = self._cached(key)
        if cached is not None:
            return cached

        # `vector_only` is a *scoring* ablation, not a switch that turns the
        # vector term on: the term is on whenever the embedder works. The flag
        # is applied in `_build_hits`. With no vectors the arm has nothing to
        # score, and `MemoryIndex` would have raised rather than answered from
        # the keyword term — so it returns the empty arm too.
        q_vec = await self._query_vector(query)
        if "vector_only" in ablation and q_vec is None:
            return []
        rows = await asyncio.to_thread(
            self._gather_sync, query, q_vec, origins, top_k, require_daily=False
        )
        pairs = await self._rank(
            query, rows, ablation=ablation, escalate=False, vectors=q_vec is not None
        )
        if "no_mmr" in ablation:
            hits = [hit for hit, _ in pairs[: mrr_top_k or top_k]]
        else:
            hits = mmr_select(pairs, top_k=mrr_top_k or top_k)
        self._store(key, hits)
        return hits

    async def escalate(self, query: str, *, top_k: int = 5, mrr_top_k: int = 5) -> list[MemoryHit]:
        """Escalation lane — direct scan of daily notes with decay disabled."""
        key = self._cache_key(
            query=query, top_k=top_k, mrr_top_k=mrr_top_k, origins=["escalate"], ablation=()
        )
        cached = self._cached(key)
        if cached is not None:
            return cached

        q_vec = await self._query_vector(query)
        rows = await asyncio.to_thread(
            self._gather_sync, query, q_vec, None, top_k, require_daily=True
        )
        pairs = await self._rank(query, rows, ablation=set(), escalate=True, vectors=q_vec is not None)
        hits = mmr_select(pairs, top_k=mrr_top_k or top_k)
        self._store(key, hits)
        return hits

    async def stats(self) -> dict:
        def work() -> dict:
            conn = self._require()
            with self._lock:
                total = conn.execute("SELECT count(*) FROM memory_chunks").fetchone()[0]
                by_origin = dict(
                    conn.execute(
                        "SELECT origin, count(*) FROM memory_chunks GROUP BY origin"
                    ).fetchall()
                )
                embedded = conn.execute(
                    "SELECT count(*) FROM memory_chunks WHERE embedding IS NOT NULL"
                ).fetchone()[0]
            return {
                "total_chunks": int(total),
                "by_origin": {k: int(v) for k, v in by_origin.items()},
                "embedded_chunks": int(embedded),
                "backend": "sqlite",
                "location": str(self.db_path),
                "vectors": embedded > 0,
                # Empty until a probe has failed; then it is the reason. A bare
                # `vectors: false` cannot tell "no embedding provider" from
                # "nothing indexed yet", and a caller that tells the owner what
                # to fix needs the difference.
                "vectors_reason": self._vectors_reason or "",
            }

        return await asyncio.to_thread(work)

    async def list_chunks(self) -> list[dict]:
        def work() -> list[dict]:
            conn = self._require()
            with self._lock:
                rows = conn.execute(
                    """
                    SELECT path, chunk_index, content, origin, importance,
                           supersedes, trigger_phrases, observed_at, evergreen
                    FROM memory_chunks
                    ORDER BY observed_at DESC
                    """
                ).fetchall()
            return [
                {
                    "path": r[0],
                    "chunk_index": r[1],
                    "content": r[2],
                    "origin": r[3],
                    "importance": r[4],
                    "supersedes": r[5],
                    "trigger_phrases": json.loads(r[6] or "[]"),
                    "observed_at": _as_date(r[7]),
                    "evergreen": bool(r[8]),
                }
                for r in rows
            ]

        return await asyncio.to_thread(work)

    async def nearest(self, text: str, *, top_k: int = 3) -> list[dict]:
        """Raw cosine neighbors (no scoring) — for dedupe checks.

        Exact scan over the embedded rows; see the module docstring for the
        bound. Returns an empty list (not an error) when vectors are off, since
        "no duplicates found" and "cannot check" are reported by `stats()`.
        """
        q_vec = await self._query_vector(text)
        if q_vec is None:
            return []

        def work() -> list[dict]:
            conn = self._require()
            with self._lock:
                embedded = conn.execute(
                    """
                    SELECT id, path, content, embedding FROM memory_chunks
                    WHERE embedding IS NOT NULL
                    """
                ).fetchall()
            scored = [
                (_cosine(q_vec, _unblob(r[3])), r[1], r[2])
                for r in embedded
                if _unblob(r[3]) is not None
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            return [
                {"path": p, "content": c, "cos": round(float(sim), 6)}
                for sim, p, c in scored[:top_k]
            ]

        return await asyncio.to_thread(work)
