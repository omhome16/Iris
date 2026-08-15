"""Reindexer — syncs the Markdown files into the Postgres index.

Runs at boot and on file change. Files are the source of truth; the index is
rebuildable. Provenance is derived from *which tier* a file belongs to:
- MEMORY.md / USER.md  → owner origin, evergreen (never decayed)
- memory/YYYY-MM-DD.md → agent origin, dated (recency-decayed)
- skills/*.md          → agent origin, evergreen (procedural)
- AGENTS.md, DREAMS.md, workspace README → not indexed (instructions are
  always injected; dreams are for human reading)
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from iris.memory.chunking import chunk_text, estimate_tokens
from iris.memory.files import WorkspaceFiles
from iris.memory.index import ChunkRecord, MemoryIndex
from iris.memory.provenance import Origin, Provenance


class Reindexer:
    def __init__(self, files: WorkspaceFiles, index: MemoryIndex) -> None:
        self.files = files
        self.index = index

    def tier_for(self, rel_path: str) -> tuple[Origin, bool]:
        if rel_path in ("MEMORY.md", "USER.md"):
            return Origin.OWNER, True
        if rel_path.startswith("memory/"):
            return Origin.AGENT, False
        if rel_path.startswith("skills/"):
            return Origin.AGENT, True
        return Origin.SYSTEM, False

    def iter_indexable(self) -> list[tuple[str, str, Origin, bool]]:
        out: list[tuple[str, str, Origin, bool]] = []
        for path in self.files.root.rglob("*.md"):
            if path.name in ("AGENTS.md", "DREAMS.md", "README.md") or ".dreams" in path.parts:
                continue
            rel = path.relative_to(self.files.root).as_posix()
            origin, evergreen = self.tier_for(rel)
            if origin is Origin.SYSTEM:
                continue
            out.append((rel, path.read_text(encoding="utf-8"), origin, evergreen))
        return out

    async def reindex_all(self) -> int:
        """Full resync. Returns number of chunks indexed."""
        indexed = 0
        for rel, text, origin, evergreen in self.iter_indexable():
            indexed += await self._index_file(rel, text, origin, evergreen)
        return indexed

    async def _index_file(self, rel: str, text: str, origin: Origin, evergreen: bool) -> int:
        if not text.strip():
            return 0
        chunks = chunk_text(text)
        records = []
        for i, chunk in enumerate(chunks):
            records.append(
                ChunkRecord(
                    path=rel,
                    chunk_index=i,
                    content=chunk,
                    provenance=Provenance(origin=origin, source=rel),
                    evergreen=evergreen,
                )
            )
        await self.index.delete_file_chunks(rel)
        await self.index.upsert_chunks(records)
        return len(records)

    async def index_daily_note(self, rel: str = "") -> None:
        """Incremental: index a newly-appended daily note (or the current one)."""
        for r, text, origin, evergreen in self.iter_indexable():
            if r.startswith("memory/") and (not rel or r == rel):
                await self._index_file(r, text, origin, evergreen)