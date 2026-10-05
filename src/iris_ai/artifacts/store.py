"""Approved component bytes live under `components/.store/sha256-<digest>/`.

The folder under `components/<kind>/<name>/` stays editable. Loading uses the
store copy when the lock pins a digest and that copy still matches it. A change
to the editable folder does not change what runs until the owner pins again.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from iris_ai.plug import canonical_files, component_digest, components_root, local_folder


def store_root(root: Path | None = None) -> Path:
    return components_root(root) / ".store"


def artifact_dir(digest: str, *, root: Path | None = None) -> Path:
    return store_root(root) / f"sha256-{digest}"


def ingest(folder: Path, *, root: Path | None = None) -> str:
    """Copy the canonical bytes into the store. Returns the digest."""
    digest = component_digest(folder)
    dest = artifact_dir(digest, root=root)
    if dest.is_dir() and component_digest(dest) == digest:
        return digest
    parent = store_root(root)
    parent.mkdir(parents=True, exist_ok=True)
    tmp = parent / f".tmp-{digest[:12]}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir()
    try:
        for relative, payload in canonical_files(folder):
            target = tmp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
        copied = component_digest(tmp)
        if copied != digest:
            raise RuntimeError(f"stored copy hashed {copied[:12]}, source hashed {digest[:12]}")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    except Exception:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
        raise
    return digest


def gc(*, root: Path | None = None) -> list[str]:
    """Delete stored trees no lock row still names. Returns the removed digests."""
    from iris_ai.components.lock import read_lock

    keep: set[str] = set()
    for row in (read_lock().get("kinds") or {}).values():
        if not isinstance(row, dict):
            continue
        if row.get("digest"):
            keep.add(str(row["digest"]))
        for item in row.get("history") or []:
            if isinstance(item, dict) and item.get("digest"):
                keep.add(str(item["digest"]))
    base = store_root(root)
    if not base.is_dir():
        return []
    removed: list[str] = []
    for child in base.iterdir():
        if not child.is_dir() or not child.name.startswith("sha256-"):
            continue
        digest = child.name.removeprefix("sha256-")
        if digest in keep:
            continue
        shutil.rmtree(child)
        removed.append(digest)
    return removed


def verify(digest: str, *, root: Path | None = None) -> bool:
    """True when the stored tree exists and still hashes to `digest`."""
    if not digest:
        return False
    folder = artifact_dir(digest, root=root)
    return folder.is_dir() and component_digest(folder) == digest


def executable_folder(kind: str, name: str, *, root: Path | None = None) -> Path | None:
    """The directory to import. The store wins when the pin still verifies."""
    folder = local_folder(kind, name, root=root)
    if folder is None:
        return None
    from iris_ai.components.lock import read_lock

    row = (read_lock().get("kinds") or {}).get(kind) or {}
    pinned = str(row.get("digest") or "")
    if row.get("active") == name and pinned and verify(pinned, root=root):
        return artifact_dir(pinned, root=root)
    return folder
