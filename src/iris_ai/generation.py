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
    # Memory is a component. A reload has to move the store and the reindexer
    # with the rest of the generation, or the new backend waits for a restart.
    "index",
    "reindexer",
    "forgetting",
    "_pending_memory_connect",
    "_retired_index",
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
    if close is None or not callable(close):
        return
    try:
        result = close()
    except Exception:  # noqa: BLE001 - closing a retired host must not break reload
        return
    if result is not None and hasattr(result, "close"):
        # An async close() returns a coroutine. Drop it here; the caller that
        # owns the loop awaits the retired index after a successful swap.
        result.close()


def _rebind_memory(scratch: Any, runtime: Any) -> None:
    """Point the next generation at the configured store when the name changed."""
    identity = getattr(runtime, "harness_identity", None) or {}
    old = ""
    if isinstance(identity, dict):
        old = str((identity.get("memory") or {}).get("name") or "")
    from iris_ai.config import settings

    new = str(settings.memory_backend or "")
    if not old or not new or old == new:
        return
    if getattr(runtime, "llm", None) is None or getattr(runtime, "files", None) is None:
        return
    from iris_ai.engine import _memory_index
    from iris_ai.memory.forgetting import ForgettingEngine
    from iris_ai.memory.indexer import Reindexer

    index = _memory_index(runtime.llm, getattr(runtime, "jev", None), None, files=runtime.files)
    previous = getattr(runtime, "index", None)
    scratch.index = index
    scratch.reindexer = Reindexer(runtime.files, index, runtime.llm)
    scratch.forgetting = ForgettingEngine(index)
    dreams = getattr(scratch, "dreams", None)
    if dreams is not None and hasattr(dreams, "index"):
        dreams.index = index
        deep = getattr(dreams, "deep", None)
        if deep is not None and hasattr(deep, "index"):
            deep.index = index
    scratch._pending_memory_connect = True
    scratch._retired_index = previous if previous is not index else None


async def finish_memory_swap(runtime: Any) -> None:
    """Connect and reindex a store that `swap_components` staged."""
    import inspect

    if not getattr(runtime, "_pending_memory_connect", False):
        return
    index = getattr(runtime, "index", None)
    if index is not None and hasattr(index, "connect"):
        await index.connect()
    reindexer = getattr(runtime, "reindexer", None)
    if reindexer is not None:
        try:
            await reindexer.reindex_all()
        except Exception as exc:  # noqa: BLE001 - a bad file must not undo the swap
            import logging

            logging.getLogger("iris.generation").warning("reindex after memory swap skipped: %s", exc)
    retired = getattr(runtime, "_retired_index", None)
    if retired is not None and retired is not index:
        close = getattr(retired, "close", None)
        if callable(close):
            result = close()
            if inspect.isawaitable(result):
                await result
    runtime._retired_index = None
    runtime._pending_memory_connect = False


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
        _rebind_memory(scratch, runtime)
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
