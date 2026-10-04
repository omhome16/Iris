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
