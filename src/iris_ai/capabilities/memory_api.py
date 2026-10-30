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


class HitRecord:
    """One recall hit, in the shape the core reads.

    A store that is not the built-in index may return plain dicts (a plugin
    written against this module has no reason to import `MemoryHit`, which
    lives beside the SQLite and Postgres implementations). Every consumer in
    the core reads attributes, so a dict hit raised AttributeError in the
    middle of a turn. `as_hit` narrows both shapes here, once.
    """

    __slots__ = (
        "chunk_index",
        "content",
        "decay",
        "evergreen",
        "imp_mult",
        "importance",
        "lane",
        "observed_at",
        "origin",
        "path",
        "relevance",
        "score",
    )

    def __init__(
        self,
        *,
        content: str = "",
        path: str = "",
        score: float = 0.0,
        importance: float = 0.0,
        origin: Any = None,
        observed_at: Any = None,
        evergreen: bool = False,
        chunk_index: int = -1,
        lane: str = "default",
        relevance: float = 0.0,
        decay: float = 1.0,
        imp_mult: float = 1.0,
    ) -> None:
        self.content = content
        self.path = path
        self.score = score
        self.importance = importance
        self.origin = origin
        self.observed_at = observed_at
        self.evergreen = evergreen
        self.chunk_index = chunk_index
        self.lane = lane
        self.relevance = relevance
        self.decay = decay
        self.imp_mult = imp_mult

    def __getitem__(self, key: str) -> Any:
        """Stay readable as a mapping for a caller that still treats hits as dicts."""
        return getattr(self, key)

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"HitRecord(path={self.path!r}, score={self.score!r})"


def _origin_of(value: Any) -> Any:
    """`Origin` without importing the store module that defines it."""
    from iris_ai.memory.provenance import Origin

    if isinstance(value, Origin):
        return value
    try:
        return Origin(str(value))
    except ValueError:
        return Origin.UNTRUSTED if value else Origin.AGENT


def as_hit(item: object, *, lane: str = "default") -> Any:
    """Narrow one recall result to the attribute shape the core reads.

    A `MemoryHit` from the built-in index passes through untouched. A dict (or
    any object missing the basics) is lifted into a `HitRecord`.
    """
    if item is None:
        return None
    if not isinstance(item, dict) and all(
        hasattr(item, name) for name in ("content", "path", "score", "origin")
    ):
        return item
    read = item.get if isinstance(item, dict) else (lambda key, default=None: getattr(item, key, default))
    return HitRecord(
        content=str(read("content", "") or ""),
        path=str(read("path", "") or ""),
        score=float(read("score", 0.0) or 0.0),
        importance=float(read("importance", 0.0) or 0.0),
        origin=_origin_of(read("origin", None)),
        observed_at=read("observed_at", None),
        evergreen=bool(read("evergreen", False)),
        chunk_index=int(read("chunk_index", -1) if read("chunk_index", -1) is not None else -1),
        lane=str(read("lane", lane) or lane),
        relevance=float(read("relevance", 0.0) or 0.0),
        decay=float(read("decay", 1.0) or 1.0),
        imp_mult=float(read("imp_mult", 1.0) or 1.0),
    )


def as_hits(items: list) -> list:
    return [hit for hit in (as_hit(item) for item in items or []) if hit is not None]
