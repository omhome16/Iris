"""The memory protocols, with no store imported.

The component sandbox imports this while it checks a folder. Importing the
registry would pull SQLite and Postgres into that jail.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger("iris.memory")

# Every store can answer these. Optional protocols live beside them.
REQUIRED: tuple[str, ...] = (
    "connect",
    "close",
    "search",
    "upsert_chunks",
    "replace_file_chunks",
    "delete_file_chunks",
    "stats",
)


@runtime_checkable
class MemoryBackend(Protocol):
    """The seven methods every store can answer."""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def search(
        self,
        query: str,
        *,
        top_k: int = 20,
        mrr_top_k: int = 5,
        require_origin: set[Any] | None = None,
        ablation: set[str] | None = None,
    ) -> list: ...

    async def stats(self) -> dict: ...

    async def upsert_chunks(self, records: list) -> None: ...

    async def delete_file_chunks(self, path: str) -> None: ...

    async def replace_file_chunks(self, path: str, records: list) -> None: ...


@runtime_checkable
class MemoryMaintenance(Protocol):
    """Optional upkeep. Dreaming, rot, and forget-confirm ask before they call."""

    async def nearest(self, text: str, *, top_k: int = 3) -> list: ...

    async def list_chunks(self) -> list: ...

    async def forget_entry(self, path: str, chunk_index: int) -> None: ...


@runtime_checkable
class MemoryEscalation(Protocol):
    """Optional second recall lane. The default lane is `search`."""

    async def escalate(self, query: str, *, top_k: int = 5, mrr_top_k: int = 5) -> list: ...


def offers(backend: object, protocol: type) -> bool:
    """True when `backend` implements `protocol` and does not decline it.

    `runtime_checkable` only looks for the names. `MarkdownIndex` inherits
    raising stubs from the degraded stand-in and sets `memory_declines` so
    callers skip those stubs instead of reaching them.
    """
    if not isinstance(backend, protocol):
        return False
    declined: set[str] = set()
    for cls in type(backend).__mro__:
        found = cls.__dict__.get("memory_declines")
        if found:
            declined.update(found)
            break
    needed = set(getattr(protocol, "__protocol_attrs__", ()))
    return not (needed & declined)


def decline(backend: object, what: str) -> None:
    """One log line when a caller skips a method the backend will not answer."""
    log.info("memory backend %s declines %s", type(backend).__name__, what)
