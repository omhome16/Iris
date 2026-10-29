"""The memory protocols, with no store imported.

The component sandbox imports this while it checks a folder. Importing the
registry would pull SQLite and Postgres into that jail.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from iris_ai.memory.provenance import Origin

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


@dataclass(slots=True)
class HitRecord:
    """The attribute shape core recall reads, including when a store returns a dict.

    A real `MemoryHit` is not rebuilt. A mapping is lifted into this record so
    `hit.path` and `hit["path"]` both work. Extra keys (`via`, `cos`, `seq`)
    stay reachable through the mapping shim.
    """

    content: str
    path: str
    score: float = 0.0
    origin: Origin = Origin.AGENT
    observed_at: object = None
    chunk_index: int = -1
    lane: str = "default"
    importance: float = 0.0
    evergreen: bool = False
    relevance: float = 0.0
    decay: float = 1.0
    imp_mult: float = 1.0
    extra: dict[str, Any] = field(default_factory=dict)

    def __getitem__(self, key: str) -> Any:
        if key in _HIT_FIELDS:
            return getattr(self, key)
        if key in self.extra:
            return self.extra[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default


_HIT_FIELDS = frozenset(
    {
        "content",
        "path",
        "score",
        "origin",
        "observed_at",
        "chunk_index",
        "lane",
        "importance",
        "evergreen",
        "relevance",
        "decay",
        "imp_mult",
    }
)


def _origin(value: object) -> Origin:
    if isinstance(value, Origin):
        return value
    text = str(getattr(value, "value", value) or "").strip().lower()
    if not text:
        return Origin.AGENT
    try:
        return Origin(text)
    except ValueError:
        return Origin.AGENT


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _hit_from_mapping(raw: Mapping) -> HitRecord:
    score = raw.get("score", raw.get("cos", 0.0))
    chunk = raw.get("chunk_index", None)
    try:
        chunk_index = -1 if chunk is None else int(chunk)
    except (TypeError, ValueError):
        chunk_index = -1
    known = {
        "content": str(raw.get("content") or ""),
        "path": str(raw.get("path") or ""),
        "score": _number(score),
        "origin": _origin(raw.get("origin")),
        "observed_at": raw.get("observed_at"),
        "chunk_index": chunk_index,
        "lane": str(raw.get("lane") or "default"),
        "importance": _number(raw.get("importance")),
        "evergreen": bool(raw.get("evergreen") or False),
        "relevance": _number(raw.get("relevance")),
        "decay": _number(raw.get("decay"), 1.0),
        "imp_mult": _number(raw.get("imp_mult"), 1.0),
    }
    extra = {str(key): value for key, value in raw.items() if str(key) not in known}
    return HitRecord(**known, extra=extra)


def as_hit(hit: object) -> object:
    """Leave an attribute hit alone. Lift a dict into `HitRecord`.

    `MemoryHit` is not imported here. This module stays free of the vector
    index so the component jail can import it.
    """
    if isinstance(hit, HitRecord):
        return hit
    if isinstance(hit, Mapping):
        return _hit_from_mapping(hit)
    if hasattr(hit, "content") and hasattr(hit, "path"):
        return hit
    return HitRecord(content="" if hit is None else str(hit), path="")


def as_hits(hits: object) -> list:
    """Normalize one search result. A lone mapping is one hit, not its keys."""
    if not hits:
        return []
    if isinstance(hits, Mapping):
        return [as_hit(hits)]
    try:
        return [as_hit(hit) for hit in hits]  # type: ignore[union-attr]
    except TypeError:
        return [as_hit(hits)]
