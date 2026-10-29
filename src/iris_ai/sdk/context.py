"""The object a component is constructed with.

v1 receives capabilities and never the runtime. Passing a runtime raises TypeError.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any


class Clock:
    def now(self) -> datetime:
        from iris_ai.timeutil import now

        return now()

    def today(self) -> date:
        from iris_ai.timeutil import today

        return today()


class FixedClock(Clock):
    """The clock checks and benchmarks use, so a run does not depend on the wall."""

    def __init__(self, today: date) -> None:
        self._today = today

    def today(self) -> date:
        return self._today

    def now(self) -> datetime:
        return datetime.combine(self._today, datetime.min.time())


class ModelAccess:
    """Ledgered model calls, capped per turn. Components do not see the client."""

    def __init__(self, llm: Any, *, cap: int = 4, methods: frozenset[str] | None = None) -> None:
        self._llm = llm
        self.cap = cap
        self.used = 0
        self.methods = frozenset(methods) if methods is not None else frozenset({"llm.complete", "llm.embed"})

    def _take(self, method: str) -> None:
        if method not in self.methods:
            raise PermissionError(f"{method} is not granted")
        if self.used >= self.cap:
            raise RuntimeError(f"llm cap of {self.cap} calls reached")
        self.used += 1

    async def complete(self, prompt: str, *, tier: str = "cheap") -> str:
        self._take("llm.complete")
        if hasattr(self._llm, "complete"):
            result = await self._llm.complete(prompt, tier=tier)
            return str(getattr(result, "text", result))
        raise RuntimeError("no model is configured")

    async def embed(self, text: str) -> list[float]:
        self._take("llm.embed")
        vector: Any = None
        if hasattr(self._llm, "embed_one"):
            vector = await self._llm.embed_one(text)
        elif hasattr(self._llm, "embed"):
            vector = await self._llm.embed(text)
        else:
            return []
        if vector and isinstance(vector[0], (list, tuple)):
            vector = vector[0]
        return [float(item) for item in vector]


class MemoryAccess:
    def __init__(self, index: Any, files: Any = None, *, methods: frozenset[str] | None = None) -> None:
        self._index = index
        self._files = files
        self.methods = (
            frozenset(methods)
            if methods is not None
            else frozenset({"memory.search", "memory.recent", "memory.curated"})
        )

    def _allow(self, method: str) -> None:
        if method not in self.methods:
            raise PermissionError(f"{method} is not granted")

    async def search(self, query: str, *, top_k: int = 5) -> list:
        self._allow("memory.search")
        hits = await self._index.search(query, top_k=top_k)
        return [_item(hit) for hit in hits]

    async def recent(self, *, limit: int = 10) -> list:
        self._allow("memory.recent")
        if not hasattr(self._index, "list_chunks"):
            logging.getLogger("iris.memory").info(
                "memory backend %s declines list_chunks; recent is empty",
                type(self._index).__name__,
            )
            return []
        rows = await self._index.list_chunks()
        from iris_ai.sdk.types import MemoryItem

        items = []
        for row in rows[:limit]:
            items.append(
                MemoryItem(
                    id=f"{row.get('path', '')}::{row.get('chunk_index', 0)}",
                    content=str(row.get("content", "")),
                    path=str(row.get("path", "")),
                    observed_at=date.today(),
                    origin=str(row.get("origin", "")),
                )
            )
        return items

    def curated(self) -> str:
        self._allow("memory.curated")
        if self._files is None:
            return ""
        return self._files.bootstrap_memory()


def _contained(root: Path, rel: str) -> Path:
    """Resolve `rel` inside `root`. Refuse traversal before the path is touched."""
    if not isinstance(rel, str) or not rel or rel in {".", "/", "\\"}:
        raise PermissionError(rel)
    if "\\" in rel or rel.startswith("/") or (len(rel) > 1 and rel[1] == ":"):
        raise PermissionError(rel)
    path = Path(rel)
    if path.is_absolute() or path.drive or any(part in {"..", "~"} for part in path.parts):
        raise PermissionError(rel)
    resolved = (root / path).resolve()
    base = root.resolve()
    if resolved != base and base not in resolved.parents:
        raise PermissionError(rel)
    return resolved


class WorkspaceAccess:
    """Reads inside the workspace. Refuses `.env` and `config/`."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def read(self, rel: str) -> str:
        path = _contained(self.root, rel)
        relative = path.relative_to(self.root.resolve())
        if path.name == ".env" or "config" in relative.parts or relative.parts[:1] == (".env",):
            raise PermissionError(rel)
        return path.read_text(encoding="utf-8")


class StateDir:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return _contained(self.root, name)


def _item(hit: Any):
    from iris_ai.sdk.types import MemoryItem

    path = str(getattr(hit, "path", "") or (hit.get("path") if isinstance(hit, dict) else ""))
    index = getattr(hit, "chunk_index", None)
    if index is None and isinstance(hit, dict):
        index = hit.get("chunk_index", 0)
    observed = getattr(hit, "observed_at", None) or date.today()
    if isinstance(observed, datetime):
        observed = observed.date()
    origin = getattr(hit, "origin", "")
    origin_text = getattr(origin, "value", origin)
    why = (
        ("relevance", float(getattr(hit, "relevance", 0) or 0)),
        ("decay", float(getattr(hit, "decay", 0) or 0)),
        ("importance", float(getattr(hit, "importance", 0) or 0)),
    )
    return MemoryItem(
        id=f"{path}::{index if index is not None else 0}",
        content=str(getattr(hit, "content", "") if not isinstance(hit, dict) else hit.get("content", "")),
        path=path,
        observed_at=observed if isinstance(observed, date) else date.today(),
        origin=str(origin_text),
        importance=float(getattr(hit, "importance", 0) or 0),
        score=float(getattr(hit, "score", 0) or 0),
        why=why,
    )


class ComponentContext:
    """v1 capabilities. A positional runtime is refused."""

    def __init__(self, runtime: Any = None, options: Mapping[str, Any] | None = None, **extra: Any) -> None:
        if runtime is not None and "api_version" not in extra:
            raise TypeError(
                "ComponentContext(runtime) was removed in 0.6. "
                "Declare permissions and construct with capabilities."
            )
        api = str(extra.pop("api_version", "iris/v1"))
        self.api_version = api
        self.kind = str(extra.pop("kind", ""))
        self.name = str(extra.pop("name", ""))
        self.log = extra.pop("log", logging.getLogger("iris.component"))
        self.clock = extra.pop("clock", Clock())
        self.grants = frozenset(extra.pop("grants", ()) or ())
        self._llm = extra.pop("llm", None)
        self._memory = extra.pop("memory", None)
        self._files = extra.pop("files", None)
        self._state = extra.pop("state", None)
        merged = dict(options or {})
        merged.update(extra)
        self.options: Mapping[str, Any] = merged
        self.runtime = None

    @property
    def llm(self) -> Any:
        return self._llm

    @property
    def memory(self) -> Any:
        return self._memory

    @property
    def files(self) -> Any:
        return self._files

    @property
    def state(self) -> Any:
        return self._state
