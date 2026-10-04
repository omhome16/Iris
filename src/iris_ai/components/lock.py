"""`config/components.lock` version 2.

v1 locks are `{kind: {active, previous, fails}}`. Reading one migrates it.
A local or installed component loads only at the digest the owner approved.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def lock_path(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from iris_ai.config import settings

    return Path(settings.harness_config).parent / "components.lock"


def read_lock(path: Path | None = None) -> dict[str, Any]:
    path = path or lock_path()
    if not path.is_file():
        return {"version": 2, "kinds": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 2, "kinds": {}}
    if not isinstance(data, dict):
        return {"version": 2, "kinds": {}}
    if data.get("version") == 2 and isinstance(data.get("kinds"), dict):
        return data
    kinds: dict[str, Any] = {}
    for kind, row in data.items():
        if kind == "version" or not isinstance(row, dict):
            continue
        kinds[kind] = {
            "active": row.get("active", ""),
            "previous": row.get("previous", ""),
            "fails": int(row.get("fails") or 0),
            "source": row.get("source", ""),
            "digest": row.get("digest", ""),
            "version": row.get("version", ""),
            "api_version": row.get("api_version", ""),
            "permissions": list(row.get("permissions") or []),
            "approved_at": row.get("approved_at", ""),
            "approved_by": row.get("approved_by", ""),
            "pipeline": list(row.get("pipeline") or []),
        }
    return {"version": 2, "kinds": kinds}


def write_lock(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 2, "kinds": data.get("kinds", {})}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def kind_row(data: dict[str, Any], kind: str) -> dict[str, Any]:
    kinds = data.setdefault("kinds", {})
    row = kinds.get(kind)
    if not isinstance(row, dict):
        row = {
            "active": "",
            "previous": "",
            "fails": 0,
            "source": "",
            "digest": "",
            "version": "",
            "api_version": "",
            "permissions": [],
            "approved_at": "",
            "approved_by": "",
            "pipeline": [],
        }
        kinds[kind] = row
    return row


def pin(
    kind: str,
    name: str,
    *,
    source: str,
    digest: str,
    version: str = "",
    api_version: str = "",
    permissions: list[str] | None = None,
    approved_by: str = "owner",
    path: Path | None = None,
) -> dict[str, Any]:
    data = read_lock(path)
    row = kind_row(data, kind)
    if row.get("active") and row.get("active") != name:
        row["previous"] = row["active"]
    row["active"] = name
    row["source"] = source
    row["digest"] = digest
    row["version"] = version
    row["api_version"] = api_version
    row["permissions"] = list(permissions or [])
    row["approved_at"] = datetime.now(UTC).isoformat()
    row["approved_by"] = approved_by
    row["fails"] = 0
    write_lock(data, path)
    return row


def drifted(kind: str, *, digest: str, path: Path | None = None) -> bool:
    """True when a pin exists and the bytes no longer match it."""
    row = read_lock(path)["kinds"].get(kind) or {}
    pinned = str(row.get("digest") or "")
    if not pinned:
        return False
    return pinned != digest
