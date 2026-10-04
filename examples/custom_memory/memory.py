"""Example: a JSON file used as a memory backend.

It implements every method the kernel may call, including `nearest` and
`list_chunks`. Dreaming, rot reports, and forget-confirm need those two.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class JsonMemory:
    def __init__(
        self,
        path: Path | str = "memory.json",
        llm: object | None = None,
        reranker: object | None = None,
        **_unused: Any,
    ) -> None:
        self.path = Path(path)
        self.llm = llm
        self.reranker = reranker
        self.rows: list[dict] = []
        if self.path.is_file():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            self.rows = loaded if isinstance(loaded, list) else []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.rows), encoding="utf-8")

    def add(self, text: str) -> None:
        self.rows.append({"content": text, "path": "memory.json", "chunk_index": len(self.rows)})
        self._save()

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self._save()

    async def search(self, query: str, **_kwargs: Any) -> list[dict]:
        needle = query.lower()
        hits = [row for row in self.rows if needle in json.dumps(row).lower()]
        return hits[: int(_kwargs.get("top_k") or 5)]

    async def escalate(self, query: str, **kwargs: Any) -> list[dict]:
        return await self.search(query, **kwargs)

    async def stats(self) -> dict:
        return {"backend": "json", "total_chunks": len(self.rows), "location": str(self.path)}

    async def upsert_chunks(self, records: list) -> None:
        for record in records:
            self.rows.append(_as_row(record))
        self._save()

    async def delete_file_chunks(self, path: str) -> None:
        self.rows = [row for row in self.rows if row.get("path") != path]
        self._save()

    async def replace_file_chunks(self, path: str, records: list) -> None:
        await self.delete_file_chunks(path)
        await self.upsert_chunks(records)

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        self.rows = [
            row
            for row in self.rows
            if not (row.get("path") == path and row.get("chunk_index") == chunk_index)
        ]
        self._save()

    async def nearest(self, text: str, *, top_k: int = 3) -> list[dict]:
        hits = await self.search(text, top_k=top_k)
        return [{**hit, "cos": 1.0 if text.lower() in json.dumps(hit).lower() else 0.0} for hit in hits]

    async def list_chunks(self) -> list[dict]:
        return list(self.rows)


def _as_row(record: object) -> dict:
    if isinstance(record, dict):
        return dict(record)
    return {
        "path": getattr(record, "path", ""),
        "chunk_index": getattr(record, "chunk_index", 0),
        "content": getattr(record, "content", ""),
        "importance": getattr(record, "importance", 0.0),
    }
