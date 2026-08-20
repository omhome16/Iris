"""Turn traces — a lightweight JSONL audit trail for the dashboard.

One line per chat turn: who asked, what tools ran, how long it took, and the
reply. Nothing sensitive beyond what the owner already said in chat; entries
are truncated. Rotates at trace_max_bytes (one generation kept).
"""

from __future__ import annotations

import json
from pathlib import Path

from iris.config import settings


class TraceLogger:
    def __init__(self, path: Path, max_bytes: int | None = None) -> None:
        self.path = path
        self.max_bytes = max_bytes or settings.trace_max_bytes

    def record(self, entry: dict) -> None:
        """Append one trace line, rotating when the file outgrows its budget."""
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            old = self.path.with_suffix(".jsonl.1")
            if old.exists():
                old.unlink()
            self.path.rename(old)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def recent(self, limit: int = 20) -> list[dict]:
        """Newest-first traces (best-effort; corrupt lines are skipped)."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        out: list[dict] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            if len(out) >= limit:
                break
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out