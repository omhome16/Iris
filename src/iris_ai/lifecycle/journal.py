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
    """Append one event and fsync it. Each line names the hash of the line before it."""
    target = journal_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    prev = _tail_hash(target)
    row: dict[str, Any] = {"ts": datetime.now(UTC).isoformat(), "event": event, "prev": prev, **fields}
    row["hash"] = _line_hash(prev, row)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    return row


def _line_hash(prev: str, row: dict[str, Any]) -> str:
    import hashlib

    body = {key: value for key, value in row.items() if key != "hash"}
    payload = json.dumps(body, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(f"{prev}\n{payload}".encode()).hexdigest()


def _tail_hash(path: Path) -> str:
    """The hash the next line must name. A legacy line with no hash still anchors the chain."""
    import hashlib

    if not path.is_file():
        return "0" * 64
    last = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last = line
    if not last:
        return "0" * 64
    try:
        loaded = json.loads(last)
    except json.JSONDecodeError:
        loaded = None
    if isinstance(loaded, dict) and loaded.get("hash"):
        return str(loaded["hash"])
    return hashlib.sha256(last.encode("utf-8")).hexdigest()


def verify_chain(path: Path | None = None) -> list[str]:
    """Lines whose hash or predecessor does not match. Empty when the file is intact."""
    import hashlib

    target = journal_path(path)
    if not target.is_file():
        return []
    problems: list[str] = []
    prev = "0" * 64
    for index, line in enumerate(target.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            problems.append(f"line {index} is not json")
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
            continue
        if not isinstance(loaded, dict) or not loaded.get("hash"):
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
            continue
        claimed = str(loaded.get("prev") or "")
        if claimed != prev:
            problems.append(f"line {index} does not follow the previous record")
        body = {key: value for key, value in loaded.items() if key != "hash"}
        if _line_hash(claimed, body) != loaded["hash"]:
            problems.append(f"line {index} was changed after it was written")
        prev = str(loaded["hash"])
    return problems


def recover(path: Path | None = None) -> list[str]:
    """Pins whose stored bytes are missing or no longer match. Empty when boot is consistent."""
    from iris_ai.artifacts.store import verify
    from iris_ai.components.lock import read_lock

    store_root = None
    if path is not None and path.name == "components.lock" and path.parent.name == "config":
        store_root = path.parent.parent / "components"
    problems: list[str] = []
    for kind, row in (read_lock(path).get("kinds") or {}).items():
        if not isinstance(row, dict):
            continue
        digest = str(row.get("digest") or "")
        if digest and not verify(digest, root=store_root):
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
