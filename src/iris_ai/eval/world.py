"""A temp workspace with the suite corpus indexed by SqliteIndex.

The fixture is built outside any component folder, so a simulation cannot
change the digest it is scoring.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.indexer import Reindexer
from iris_ai.memory.sqlite_index import SqliteIndex
from iris_ai.testing import HashEmbedder


def memory_record(path: str, index: int, content: str, *, observed=None):
    """One chunk the memory behaviour suite writes."""
    from datetime import datetime

    from iris_ai.memory.index import ChunkRecord
    from iris_ai.memory.provenance import Origin, Provenance

    return ChunkRecord(
        path=path,
        chunk_index=index,
        content=content,
        provenance=Provenance(origin=Origin.OWNER, observed_at=observed or datetime.now()),
        importance=1.0,
        evergreen=True,
    )


def open_memory(root: Path, *, llm=None) -> SqliteIndex:
    """A SQLite store in `root`, not yet connected. The suite calls connect."""
    from iris_ai.testing import HashEmbedder

    return SqliteIndex(Path(root) / "memory.db", llm=llm or HashEmbedder())


def apply_plan(curated: str, plan) -> str:
    """Apply a consolidation plan to curated text. The suite checks that this returns."""
    lines = curated.splitlines()
    for token in getattr(plan, "supersede", ()) or ():
        needle = str(token)
        if needle:
            lines = [line for line in lines if needle not in line]
    for item in getattr(plan, "add", ()) or ():
        content = str(getattr(item, "content", "") or "").strip()
        if content:
            lines.append(f"- {content}")
    body = "\n".join(lines)
    return body + ("\n" if body else "")


def build_world(root: Path, suite: dict) -> SqliteIndex:
    files = WorkspaceFiles(Path(root))
    lines = []
    for case in suite.get("cases") or []:
        for row in case.get("corpus") or []:
            text = str(row.get("content") or "").strip()
            if text:
                lines.append(f"- [5] {text}")
        curated = str(case.get("curated") or "").strip()
        if curated:
            lines.append(curated)
    files.memory.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    index = SqliteIndex(files.root / "config" / "memory.db", llm=HashEmbedder())
    asyncio.run(index.connect())
    asyncio.run(Reindexer(files, index, llm=None).reindex_all())
    return index
