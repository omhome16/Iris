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

import re
from datetime import date, datetime

from iris_ai.memory.chunking import chunk_text, contextualize_chunks
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.index import ChunkRecord, MemoryIndex
from iris_ai.memory.llm import LLMClient
from iris_ai.memory.provenance import Origin, Provenance

_DATE_RE = re.compile(r"memory/(\d{4})-(\d{2})-(\d{2})\.md$")


def observed_date_for(rel: str) -> date | None:
    """The date encoded in a daily-note filename — the *actual* day the note
    describes. Without this, reindexed notes would all be stamped 'today'
    and recency decay would never fire."""
    m = _DATE_RE.match(rel)
    if not m:
        return None
    try:
        return date(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None


class Reindexer:
    def __init__(self, files: WorkspaceFiles, index: MemoryIndex, llm: LLMClient | None = None) -> None:
        self.files = files
        self.index = index
        self.llm = llm

    def tier_for(self, rel_path: str) -> tuple[Origin, bool]:
        if rel_path in ("MEMORY.md", "USER.md"):
            return Origin.OWNER, True
        if rel_path.startswith("memory/"):
            return Origin.AGENT, False
        if rel_path.startswith("imports/"):
            return Origin.UNTRUSTED, False
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
            text = path.read_text(encoding="utf-8")
            # "not yet born" placeholders (USER.md/MEMORY.md before onboarding
            # or first dream) carry no facts — nothing worth indexing. The
            # marker never appears in real content, so a single occurrence is
            # enough to skip (the old check required it twice and never fired).
            if "_Empty" in text:
                continue
            out.append((rel, text, origin, evergreen))
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
        chunks = await contextualize_chunks(
            chunk_text(text),
            llm=self.llm,
            cache_dir=self.files.root / ".dreams" / "contexts",
            text=text,
        )
        observed = observed_date_for(rel)
        records = []
        for i, chunk in enumerate(chunks):
            records.append(
                ChunkRecord(
                    path=rel,
                    chunk_index=i,
                    content=chunk,
                    provenance=Provenance(
                        origin=origin,
                        source=rel,
                        observed_at=(
                            datetime.combine(observed, datetime.min.time())
                            if observed
                            else datetime.now()
                        ),
                    ),
                    evergreen=evergreen,
                )
            )
        await self.index.replace_file_chunks(rel, records)
        return len(records)

    async def index_daily_note(self, rel: str = "") -> None:
        """Incremental: index a newly-appended daily note (or the current one)."""
        for r, text, origin, evergreen in self.iter_indexable():
            if r.startswith("memory/") and (not rel or r == rel):
                await self._index_file(r, text, origin, evergreen)
