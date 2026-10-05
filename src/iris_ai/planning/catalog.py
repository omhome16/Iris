"""What a component is allowed to ask for, and the steps before it can run.

The catalog is the broker's grant list. A permission that is not on that list
is refused here, before a host is started.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from iris_ai.buildlock import inspect
from iris_ai.isolation.broker import KNOWN_GRANTS


def plan(folder: Path) -> dict:
    """A report. It does not activate the folder."""
    meta_path = folder / "component.toml"
    meta: dict = {}
    if meta_path.is_file():
        loaded = tomllib.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            meta = loaded
    requested = [str(item) for item in (meta.get("permissions") or [])]
    granted = [item for item in requested if item in KNOWN_GRANTS]
    refused = [item for item in requested if item not in KNOWN_GRANTS]
    build = inspect(folder)
    steps = [
        "check the folder",
        "ingest the artifact",
        "approve activation",
        "reload",
    ]
    if build["status"] == "build_pending":
        steps.insert(0, "provide the hash-locked wheels named in component.toml")
    if build["status"] == "refused":
        steps.insert(0, "replace refused dependencies with pinned wheels")
    return {
        "kind": str(meta.get("kind") or ""),
        "name": str(meta.get("name") or folder.name),
        "grants": granted,
        "refused_grants": refused,
        "catalog": sorted(KNOWN_GRANTS),
        "build": build["status"],
        "build_problems": build["problems"],
        "steps": steps,
    }
