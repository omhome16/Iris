"""`config/lifecycle.jsonl` — one line per install, rollback, or reload.

The turn journal rotates. This file does not. A restart can see which artifact
was approved.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def journal_path(path: Path | None = None) -> Path:
    if path is not None:
        return path
    from iris_ai.components.lock import lock_path

    return lock_path().parent / "lifecycle.jsonl"


def record(event: str, *, path: Path | None = None, **fields: Any) -> dict[str, Any]:
    """Append one event and fsync it."""
    row: dict[str, Any] = {"ts": datetime.now(UTC).isoformat(), "event": event, **fields}
    target = journal_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def recover() -> list[str]:
    """Pins whose stored bytes are missing or no longer match. Empty when boot is consistent."""
    from iris_ai.artifacts.store import verify
    from iris_ai.components.lock import read_lock

    problems: list[str] = []
    for kind, row in (read_lock().get("kinds") or {}).items():
        if not isinstance(row, dict):
            continue
        digest = str(row.get("digest") or "")
        if digest and not verify(digest):
            active = str(row.get("active") or kind)
            problems.append(f"{kind} {active} is pinned to {digest[:12]} but the stored copy does not verify")
    return problems


def read_events(path: Path | None = None) -> list[dict[str, Any]]:
    target = journal_path(path)
    if not target.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows
