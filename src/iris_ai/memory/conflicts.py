"""Conflicts wait for the owner. Nothing here edits MEMORY.md."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any


def conflicts_path(root: Path) -> Path:
    return Path(root) / "config" / "conflicts.jsonl"


def append_conflict(root: Path, conflict: Any) -> str:
    path = conflicts_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(conflict, dict):
        row = dict(conflict)
    else:
        row = {
            "existing": getattr(getattr(conflict, "existing", None), "content", ""),
            "incoming": getattr(getattr(conflict, "incoming", None), "content", ""),
            "reason": getattr(conflict, "reason", ""),
        }
    row.setdefault("id", uuid.uuid4().hex[:12])
    row.setdefault("status", "open")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return str(row["id"])


def list_conflicts(root: Path, *, status: str = "open") -> list[dict]:
    path = conflicts_path(root)
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if status and row.get("status") != status:
            continue
        rows.append(row)
    return rows


def apply_resolution(text: str, row: dict, decision: str) -> str:
    """Edit curated Markdown for one owner decision. `keep` changes nothing."""
    if decision == "keep":
        return text
    incoming = str(row.get("incoming") or "").strip()
    existing = str(row.get("existing") or "").strip()
    lines = text.splitlines()
    if decision == "replace" and existing:
        for index, line in enumerate(lines):
            if existing in line and "(superseded" not in line:
                lines[index] = f"{line} (superseded)"
                break
    if incoming and not any(incoming in line for line in lines):
        lines.append(f"- {incoming}" if not incoming.startswith("-") else incoming)
    body = "\n".join(lines)
    if text.endswith("\n") or not body:
        return body + ("\n" if body else "")
    return body


def resolve_conflict(root: Path, conflict_id: str, decision: str) -> dict:
    """Record the decision and edit MEMORY.md. `keep` leaves the file alone."""
    if decision not in {"keep", "replace", "both"}:
        raise ValueError("decision must be keep, replace, or both")
    path = conflicts_path(root)
    if not path.is_file():
        raise KeyError(conflict_id)
    found: dict | None = None
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("id") == conflict_id and row.get("status") == "open":
            row["status"] = decision
            found = row
        lines.append(json.dumps(row, ensure_ascii=False))
    if found is None:
        raise KeyError(conflict_id)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    memory = Path(root) / "MEMORY.md"
    if memory.is_file() and decision != "keep":
        memory.write_text(apply_resolution(memory.read_text(encoding="utf-8"), found, decision), encoding="utf-8")
    return found
