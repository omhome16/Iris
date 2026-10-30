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
    """Remember a choice. A local folder is pinned only after it passes verification."""
    from iris_ai.artifacts.store import ingest
    from iris_ai.components.lock import pin
    from iris_ai.lifecycle.journal import record
    from iris_ai.plug import _read_toml, _remember, local_folder, verification

    folder = local_folder(kind, option) if "," not in option else None
    if folder is None:
        _remember(kind, option)
        record("select", kind=kind, name=option)
        return option
    # Same door as activate: the jailed check, then the behaviour suite.
    # A failure raises and leaves the lock unpinned.
    suite, verified_at = verification(folder)
    digest = ingest(folder)
    meta = _read_toml(folder / "component.toml")
    pin(
        kind,
        option,
        source="local",
        digest=digest,
        api_version=str(meta.get("api_version") or ""),
        permissions=list(meta.get("permissions") or []),
        approved_by="owner",
        suite=suite,
        verified_at=verified_at,
    )
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
