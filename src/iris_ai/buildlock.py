"""Hash-locked wheels only.

A component may name dependencies. Each one needs a version and a sha256.
Git URLs, file URLs, source archives, and setup.py are refused. Missing wheel
bytes are `build_pending`. Nothing here downloads or executes a package.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from pathlib import Path

_REFUSED = re.compile(r"(git\+|file:|https?://|\.tar\.gz|\.zip|setup\.py)", re.IGNORECASE)


def inspect(folder: Path) -> dict:
    """Check `component.toml` dependencies and write `deps.lock` when they are legal."""
    meta_path = folder / "component.toml"
    meta: dict = {}
    if meta_path.is_file():
        loaded = tomllib.loads(meta_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            meta = loaded
    raw = meta.get("dependencies") or {}
    problems: list[str] = []
    locked: list[dict] = []
    if isinstance(raw, list):
        problems.append("dependencies must be a table of name, version, and sha256")
        raw = {}
    if not isinstance(raw, dict):
        problems.append("dependencies must be a table")
        raw = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            problems.append(f"{name} must be a table with version and sha256")
            continue
        version = str(spec.get("version") or "")
        digest = str(spec.get("sha256") or "").lower()
        if _REFUSED.search(f"{name} {version} {digest}"):
            problems.append(f"{name} is not a pinned wheel")
            continue
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            problems.append(f"{name} needs a sha256")
            continue
        wheel = folder / "wheels" / f"{name}-{version}-py3-none-any.whl"
        present = wheel.is_file()
        if present and hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
            problems.append(f"{name} wheel hash does not match")
            continue
        locked.append({"name": str(name), "version": version, "sha256": digest, "present": present})
    if problems:
        status = "refused"
    elif locked and any(not item["present"] for item in locked):
        status = "build_pending"
    else:
        status = "built"
    if status != "refused":
        (folder / "deps.lock").write_text(
            json.dumps({"status": status, "dependencies": locked}, indent=2) + "\n",
            encoding="utf-8",
        )
    return {"status": status, "problems": problems, "dependencies": locked}
