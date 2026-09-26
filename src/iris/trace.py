"""Turn traces — a lightweight JSONL audit trail for turn analysis.

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

    def write_raw(self, entry: dict) -> None:
        """Append without the content policy — for tests and migrations only.

        Nothing in `src/` should call this: the point of the policy is that there
        is one way in.
        """
        self._write(entry)

    def record(self, entry: dict) -> None:
        """Append one trace line, rotating when the file outgrows its budget.

        Every write goes through the content policy and redaction (`iris.redact`),
        so a credential passed as a tool argument cannot reach disk through this
        path regardless of which caller built the entry.
        """
        from iris import redact

        sampled = False
        if settings.trace_content_sample_rate > 0:
            import random

            sampled = random.random() < settings.trace_content_sample_rate
        entry = redact.apply_content_policy(entry, sample=sampled)
        self._write(entry)

    def _write(self, entry: dict) -> None:
        """The unconditional append (used by `record` after redaction)."""
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
