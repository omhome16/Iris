"""Keyword recall over the Markdown files themselves. No index database.

Writes are no-ops here: the files are already the source of truth. Search
walks `*.md` under the workspace and returns the lines that contain the query.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.index import MemoryHit
from iris_ai.memory.null_index import NullIndex
from iris_ai.memory.provenance import Origin


class MarkdownIndex(NullIndex):
    """Files only. `search` reads Markdown; it does not raise `MemoryUnavailable`."""

    def __init__(self, dsn: str = "", llm: object | None = None, reranker: object | None = None) -> None:
        super().__init__(dsn, llm, reranker)
        self.root = Path(settings.workspace_dir)

    async def search(self, query: str, *, top_k: int = 20, **_kwargs) -> list[MemoryHit]:
        needle = (query or "").strip().lower()
        if not needle or not self.root.is_dir():
            return []
        hits: list[MemoryHit] = []
        for path in sorted(self.root.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if needle not in text.lower():
                continue
            line = next((row.strip() for row in text.splitlines() if needle in row.lower()), text[:200])
            rel = path.relative_to(self.root).as_posix()
            hits.append(
                MemoryHit(
                    content=line[:400],
                    path=rel,
                    score=1.0,
                    importance=0.5,
                    origin=Origin.OWNER,
                    observed_at=date.today(),
                    evergreen=True,
                    chunk_index=0,
                    relevance=1.0,
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    async def escalate(self, query: str, *, top_k: int = 5, mrr_top_k: int = 5) -> list:
        return await self.search(query, top_k=top_k)

    async def stats(self) -> dict:
        count = len(list(self.root.rglob("*.md"))) if self.root.is_dir() else 0
        return {
            "backend": "markdown",
            "degraded": False,
            "total_chunks": count,
            "location": str(self.root),
        }
