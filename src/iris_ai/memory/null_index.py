"""Degraded index stand-in — used when no Postgres is reachable.

`iris chat` should still start when the database is not up: the *conversation*
is the product's front door, and the index is a rebuildable derivative of the
Markdown memory. So a degraded session gets:

- recall that **raises** `MemoryUnavailable` with the fix command in the message
  (never an empty result set, which would masquerade as "I don't remember"),
- write methods that are accepted no-ops, because the index is derived: the
  daily note still receives captures and `remember`/`note` lines, and the next
  reindex picks them up once Postgres is back,
- `stats()` that says `degraded: True` so a caller can report it.

The name mirrors langgraph's `MemorySaver`: the in-memory counterpart of the
durable, Postgres-backed component.
"""

from __future__ import annotations

from typing import Any

from iris_ai.memory.index import ChunkRecord, MemoryHit, MemoryUnavailable


class NullIndex:
    """Same surface as `MemoryIndex`, with recall disabled by design."""

    def __init__(self, dsn: str, llm: object | None = None, reranker: object | None = None) -> None:
        self.dsn = dsn
        self.llm = llm
        self.reranker = reranker

    # ── lifecycle ────────────────────────────────────────────────────────
    async def connect(self) -> None:
        """No-op: degrading is the caller's decision, not this object's."""

    async def close(self) -> None:
        """No-op — there is no pool to release."""

    def clear_cache(self) -> None:
        """No-op: nothing is cached."""

    # ── recall (always unavailable) ───────────────────────────────────────
    def _unavailable(self) -> MemoryUnavailable:
        return MemoryUnavailable(
            f"memory index unavailable — no Postgres at {self.dsn}. "
            "Start one with: docker compose up -d postgres"
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        mrr_top_k: int = 5,
        require_origin: set[Any] | None = None,
        ablation: set[str] | None = None,
    ) -> list[MemoryHit]:
        raise self._unavailable()

    async def escalate(self, query: str, *, top_k: int = 5, mrr_top_k: int = 5) -> list[MemoryHit]:
        raise self._unavailable()

    async def nearest(self, text: str, *, top_k: int = 3) -> list[dict]:
        raise self._unavailable()

    async def list_chunks(self) -> list[dict]:
        raise self._unavailable()

    # ── reporting ────────────────────────────────────────────────────────
    async def stats(self) -> dict:
        """The real `stats()` shape plus the degraded markers."""
        return {
            "total_chunks": 0,
            "by_origin": {},
            "degraded": True,
            "reason": f"no Postgres at {self.dsn}",
        }

    # ── write path (accepted no-ops: the index is derived) ───────────────
    async def upsert_chunks(self, records: list[ChunkRecord]) -> None:
        """No-op: Markdown is the source of truth and is written by the caller."""

    async def delete_file_chunks(self, path: str) -> None:
        """No-op."""

    async def replace_file_chunks(self, path: str, records: list[ChunkRecord]) -> None:
        """No-op."""

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        """No-op — `forget` supersedes the Markdown entry itself, not a row."""
