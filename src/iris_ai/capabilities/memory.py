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

from typing import Any

from iris_ai.capabilities.memory_api import (  # noqa: F401 - re-exported
    REQUIRED,
    MemoryBackend,
    MemoryEscalation,
    MemoryMaintenance,
    decline,
    offers,
)
from iris_ai.config import settings
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.null_index import NullIndex
from iris_ai.memory.sqlite_index import SqliteIndex
from iris_ai.registry import Registry

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
