"""`config/components.lock` version 3.

One writer. v1 locks are `{kind: {active, previous, fails}}`. v2 locks are
`{version, kinds}`. Reading either migrates it. A local component loads only
at the digest recorded here. `plug.py` used to write the v1 shape into the
same file; that split is why an activation could look unpinned.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def lock_path(root: Path | None = None) -> Path:
    if root is not None:
        return Path(root)
    from iris_ai.config import settings

    return Path(settings.harness_config).parent / "components.lock"


def _blank_row(row: dict[str, Any] | None = None) -> dict[str, Any]:
    row = row or {}
    history = row.get("history") or []
    return {
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
        "history": [item for item in history if isinstance(item, dict)],
        "probation_remaining": int(row.get("probation_remaining") or 0),
    }


def _looks_like_kind(value: object) -> bool:
    return isinstance(value, dict) and any(key in value for key in ("active", "previous", "digest", "fails"))


def normalize(data: object) -> dict[str, Any]:
    """Fold a v1, v2, or mixed file into version 3."""
    if not isinstance(data, dict):
        return {"version": 3, "kinds": {}}
    kinds: dict[str, Any] = {}
    nested = data.get("kinds") if data.get("version") in (2, 3) else None
    if isinstance(nested, dict):
        for kind, row in nested.items():
            if isinstance(row, dict):
                kinds[str(kind)] = _blank_row(row)
    for key, value in data.items():
        if key in {"version", "kinds"} or not _looks_like_kind(value):
            continue
        incoming = _blank_row(value)
        current = kinds.get(str(key))
        if current is None:
            kinds[str(key)] = incoming
            continue
        # The top-level row is what `plug.py` wrote last for the selection.
        # Keep a digest only when it still names that selection.
        if incoming.get("active") and incoming["active"] != current.get("active"):
            current["previous"] = incoming.get("previous") or current.get("active") or ""
            current["active"] = incoming["active"]
            current["fails"] = incoming["fails"]
            current["digest"] = incoming["digest"] or ""
        elif not current.get("digest") and incoming.get("digest"):
            current["digest"] = incoming["digest"]
    return {"version": 3, "kinds": kinds}


def _read_unlocked(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"version": 3, "kinds": {}}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 3, "kinds": {}}
    return normalize(loaded)


def read_lock(path: Path | None = None) -> dict[str, Any]:
    return _read_unlocked(path or lock_path())


def _write_atomic(data: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 3, "kinds": data.get("kinds", {})}
    text = json.dumps(payload, indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def write_lock(data: dict[str, Any], path: Path | None = None) -> None:
    path = path or lock_path()
    with _file_lock(path):
        _write_atomic(normalize(data) if data.get("version") != 3 else data, path)


_LOCK_TIMEOUT = 5.0


def _pid_alive(pid: int) -> bool:
    """Whether `pid` still has a process. Access denied counts as alive."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.GetLastError.restype = ctypes.c_uint32
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return kernel32.GetLastError() == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _lock_is_stale(flag: Path) -> bool:
    """A sidecar whose owner is gone can be removed. A live owner cannot."""
    try:
        pid = int(flag.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return True
    return not _pid_alive(pid)


@contextmanager
def _file_lock(path: Path, *, timeout: float | None = None) -> Iterator[None]:
    """A sidecar lock so the CLI and the TUI cannot rewrite the file together.

    A waiter that times out leaves a live sidecar alone. It removes one only
    when the recorded pid is no longer a process.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flag = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + (_LOCK_TIMEOUT if timeout is None else timeout)
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(str(flag), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() < deadline:
                time.sleep(0.02)
                continue
            if _lock_is_stale(flag):
                with contextlib.suppress(OSError):
                    flag.unlink()
                continue
            raise TimeoutError(f"{path.name} is locked") from None
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        with contextlib.suppress(OSError):
            flag.unlink()


@contextmanager
def transaction(path: Path | None = None) -> Iterator[dict[str, Any]]:
    """Read, mutate, and replace the lock. The yielded dict is version 3."""
    path = path or lock_path()
    with _file_lock(path):
        data = _read_unlocked(path)
        yield data
        _write_atomic(data, path)


def kind_row(data: dict[str, Any], kind: str) -> dict[str, Any]:
    kinds = data.setdefault("kinds", {})
    row = kinds.get(kind)
    if not isinstance(row, dict):
        row = _blank_row()
        kinds[kind] = row
    else:
        filled = _blank_row(row)
        row.clear()
        row.update(filled)
    return row


def _push_history(row: dict[str, Any]) -> None:
    active = str(row.get("active") or "")
    if not active:
        return
    history = list(row.get("history") or [])
    history.append(
        {
            "name": active,
            "digest": str(row.get("digest") or ""),
            "at": datetime.now(UTC).isoformat(),
        }
    )
    row["history"] = history[-20:]


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
    with transaction(path) as data:
        row = kind_row(data, kind)
        if row.get("active") and row.get("active") != name:
            _push_history(row)
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
        # A new pin is on probation: one failure in the next few calls rolls it back.
        row["probation_remaining"] = 3
        return dict(row)


def remember(kind: str, name: str, *, path: Path | None = None) -> dict[str, Any]:
    """Select `name` without inventing a digest. Activation pins separately."""
    with transaction(path) as data:
        row = kind_row(data, kind)
        current = str(row.get("active") or "")
        if current and current != name:
            _push_history(row)
            row["previous"] = current
            row["digest"] = ""
        elif not str(row.get("previous") or "") or row.get("previous") == name:
            row["previous"] = row.get("previous") or ""
        row["active"] = name
        row["fails"] = 0
        row["probation_remaining"] = 0
        return dict(row)


def set_selection(
    kind: str,
    name: str,
    *,
    previous: str = "",
    path: Path | None = None,
) -> dict[str, Any]:
    """Point a kind at `name`, restoring a digest from history when one exists."""
    with transaction(path) as data:
        row = kind_row(data, kind)
        if previous:
            row["previous"] = previous
        digest = ""
        for item in reversed(row.get("history") or []):
            if item.get("name") == name and item.get("digest"):
                digest = str(item["digest"])
                break
        if row.get("active") == name:
            digest = str(row.get("digest") or digest)
        row["active"] = name
        row["digest"] = digest
        row["fails"] = 0
        row["probation_remaining"] = 0
        return dict(row)


def bump_fails(kind: str, name: str, *, path: Path | None = None) -> int:
    with transaction(path) as data:
        row = kind_row(data, kind)
        if not row.get("active"):
            row["active"] = name
        row["fails"] = int(row.get("fails") or 0) + 1
        return int(row["fails"])


def probation_left(kind: str, *, path: Path | None = None) -> int:
    row = read_lock(path)["kinds"].get(kind) or {}
    return int(row.get("probation_remaining") or 0)


def tick_probation(kind: str, *, path: Path | None = None) -> None:
    """One successful call shortens the window. A kind with no pin is left alone."""
    current = read_lock(path)["kinds"].get(kind) or {}
    if int(current.get("probation_remaining") or 0) <= 0:
        return
    with transaction(path) as data:
        row = data["kinds"].get(kind)
        if not isinstance(row, dict):
            return
        left = int(row.get("probation_remaining") or 0)
        if left > 0:
            row["probation_remaining"] = left - 1


def drifted(kind: str, *, digest: str, path: Path | None = None) -> bool:
    """True when a pin exists and the bytes no longer match it."""
    row = read_lock(path)["kinds"].get(kind) or {}
    pinned = str(row.get("digest") or "")
    if not pinned:
        return False
    return pinned != digest
