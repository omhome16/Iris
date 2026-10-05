"""A runtime generation is one consistent set of component bindings.

`attach` used to write those bindings onto the live runtime one kind at a time.
`swap_components` builds the next set on a scratch object and copies it across
only after the build returns.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any

_KINDS = ("engine", "context", "memory", "persona", "capture", "consolidator", "channel")
_SWAP = (
    "pipelines",
    "context_builder",
    "capture_policy",
    "persona_choice",
    "dreams",
    "consolidator_stage",
    "harness_identity",
    "engine_name",
    "pipeline_fails",
)


def describe(section: dict | None = None) -> str:
    """A stable line per kind: `name@digest`."""
    from iris_ai.components.lock import read_lock
    from iris_ai.config import settings

    section = section or {}
    lock = read_lock().get("kinds") or {}
    lines: list[str] = []
    for kind in _KINDS:
        if kind == "memory":
            name = str(section.get("memory") or settings.memory_backend or "sqlite")
        elif kind == "channel":
            name = str(section.get("channel") or settings.channels_enabled or "terminal")
        elif kind == "engine":
            name = str(section.get("engine") or "react")
        else:
            name = str(section.get(kind) or "")
        row = lock.get(kind) or {}
        digest = str(row.get("digest") or "") if row.get("active") == name else ""
        lines.append(f"{kind}={name}@{digest}")
    return "\n".join(lines)


def _close(component: Any) -> None:
    close = getattr(component, "close", None)
    if close is None:
        return
    try:
        close()
    except Exception:  # noqa: BLE001 - closing a retired host must not break reload
        return


def generation_id(section: dict | None = None) -> str:
    return hashlib.sha256(describe(section).encode()).hexdigest()[:16]


def swap_components(runtime: Any, manifest: dict) -> str:
    """Build the next component set, then assign it. A raised error leaves `runtime`."""
    from iris_ai.components import attach

    scratch = SimpleNamespace()
    for name in dir(runtime):
        if name.startswith("_"):
            continue
        try:
            setattr(scratch, name, getattr(runtime, name))
        except (AttributeError, TypeError):
            continue
    previous = {name: getattr(runtime, name, None) for name in _SWAP}
    try:
        notice = attach(scratch, manifest)
    except Exception:
        for name in _SWAP:
            created = getattr(scratch, name, None)
            if created is not previous.get(name):
                _close(created)
        raise
    for name in _SWAP:
        if hasattr(scratch, name):
            setattr(runtime, name, getattr(scratch, name))
    section = manifest.get("components") if isinstance(manifest.get("components"), dict) else {}
    runtime.generation_id = generation_id(section)
    return notice
