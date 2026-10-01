"""Memory backend capability — the `MemoryBackend` Protocol and its registry.

The kernel asks a memory backend to search, escalate, and report; retrieval
*policy* (decay, MMR, lane choice) stays in the kernel because it is policy, not
storage. The core implementations are `MemoryIndex` (Postgres + pgvector),
registered as ``pgvector``, and `NullIndex`, registered as ``null`` (the honest
degraded backend that raises rather than returning an empty result set).

The SQLite backend in Phase 2 registers here too; nothing above this line changes
when it does.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from iris_ai.config import settings
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.null_index import NullIndex
from iris_ai.memory.sqlite_index import SqliteIndex
from iris_ai.registry import Registry

REQUIRED: tuple[str, ...] = (
    "connect",
    "close",
    "search",
    "escalate",
    "stats",
    "upsert_chunks",
    "delete_file_chunks",
    "replace_file_chunks",
    "forget_entry",
)


@runtime_checkable
class MemoryBackend(Protocol):
    """Everything the kernel may ask of a memory store."""

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

    async def escalate(self, query: str, *, top_k: int = 5, mrr_top_k: int = 5) -> list: ...

    async def stats(self) -> dict: ...

    async def upsert_chunks(self, records: list) -> None: ...

    async def delete_file_chunks(self, path: str) -> None: ...

    async def replace_file_chunks(self, path: str, records: list) -> None: ...

    async def forget_entry(self, path: str, chunk_index: int) -> None: ...


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
