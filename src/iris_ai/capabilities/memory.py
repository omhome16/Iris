"""Memory backend capability — the `MemoryBackend` Protocol and its registry.

The kernel asks every store to search and to report. Retrieval *policy*
(decay, MMR, lane choice) stays in the kernel because it is policy, not
storage. Maintenance (`nearest`, `list_chunks`, `forget_entry`) and the
escalation lane are optional protocols. A caller asks `offers` and skips the
call with one log line when the backend declines, instead of assuming the
method exists.

The core implementations are `MemoryIndex` (Postgres + pgvector), registered
as ``pgvector``, and `NullIndex`, registered as ``null`` (the honest degraded
backend that raises rather than returning an empty result set).

The SQLite backend registers here too; nothing above this line changes when a
new store does.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from iris_ai.config import settings
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.null_index import NullIndex
from iris_ai.memory.sqlite_index import SqliteIndex
from iris_ai.registry import Registry

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


#: The memory registry. `pgvector` (durable, multi-process) and `sqlite` (the
#: zero-service default) are core, as is the `null` degraded stand-in.
MEMORY_BACKENDS: Registry[MemoryBackend] = Registry("memory_backend")

MEMORY_BACKENDS.register(
    "pgvector",
    lambda **kw: MemoryIndex(**kw),
    source="core",
)


def _build_sqlite(**kw: Any) -> SqliteIndex:
    """Build the SQLite backend from the uniform boot context.

    The engine hands every memory backend the same kwargs (`dsn=…`, `llm=…`,
    `reranker=…`), because it must not know which store it is talking to. This
    backend takes its location from settings rather than the DSN, so switching
    stores stays a settings change instead of a special case at the boot site.
    """
    return SqliteIndex(path=settings.sqlite_path, llm=kw.get("llm"), reranker=kw.get("reranker"))


MEMORY_BACKENDS.register("sqlite", _build_sqlite, source="core")


def _build_evidence(**kw: Any) -> object:
    from iris_ai.catalog.memory.evidence import EvidenceMemory

    return EvidenceMemory(path=settings.sqlite_path, llm=kw.get("llm"), reranker=kw.get("reranker"))


MEMORY_BACKENDS.register("evidence-memory", _build_evidence, source="core")
MEMORY_BACKENDS.register(
    "null",
    lambda **kw: NullIndex(**kw),
    source="core",
)


def _build_markdown(**kw: Any) -> Any:
    from iris_ai.memory.markdown_index import MarkdownIndex

    return MarkdownIndex(**kw)


MEMORY_BACKENDS.register("markdown", _build_markdown, source="core")


def discover_memory_backends(enabled: set[str] | None = None) -> list[str]:
    """Register installed memory backends; returns the names that were added."""
    return [reg.name for reg in MEMORY_BACKENDS.discover("iris_ai.memory", enabled=enabled)]
