"""The one door for selecting, activating, and rolling back a component.

The CLI and the tools call this. It delegates to the existing install and
rollback functions, and it records the selection. Callers do not pin the lock
themselves.
"""

from __future__ import annotations


def activate(kind: str, name: str) -> str:
    from iris_ai.plug import activate as impl

    return impl(kind, name)


def rollback(kind: str) -> str:
    from iris_ai.plug import rollback as impl

    return impl(kind)


def select(kind: str, option: str) -> str:
    """Remember a choice and, for one local folder, pin the stored bytes."""
    from iris_ai.artifacts.store import ingest
    from iris_ai.components.lock import pin
    from iris_ai.lifecycle.journal import record
    from iris_ai.plug import _remember, local_folder

    _remember(kind, option)
    folder = local_folder(kind, option) if "," not in option else None
    if folder is None:
        record("select", kind=kind, name=option)
        return option
    digest = ingest(folder)
    pin(kind, option, source="local", digest=digest, approved_by="owner")
    record("select", kind=kind, name=option, digest=digest)
    return option


async def reload(harness: object) -> str:
    """Ask a live harness to swap components. The harness owns the runtime."""
    import inspect

    reload_fn = getattr(harness, "reload", None)
    if reload_fn is None:
        raise RuntimeError("this harness cannot reload")
    result = reload_fn()
    if inspect.isawaitable(result):
        result = await result
    return str(result or "")
