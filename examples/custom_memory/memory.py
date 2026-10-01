"""Example: a JSON file used as a tiny memory store."""

from __future__ import annotations

import json
from pathlib import Path


class JsonMemory:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.rows: list[dict] = []
        if self.path.is_file():
            self.rows = json.loads(self.path.read_text(encoding="utf-8"))

    def add(self, text: str) -> None:
        self.rows.append({"text": text})
        self.path.write_text(json.dumps(self.rows), encoding="utf-8")

    async def search(self, query: str, **_kwargs) -> list[dict]:
        needle = query.lower()
        return [row for row in self.rows if needle in row.get("text", "").lower()]
