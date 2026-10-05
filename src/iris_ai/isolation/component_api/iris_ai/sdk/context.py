"""Capability proxies. Each call is a request back to the parent broker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

_BROKER: Callable[[str, dict], Any] | None = None


def set_broker(fn: Callable[[str, dict], Any]) -> None:
    global _BROKER
    _BROKER = fn


def _request(method: str, params: dict) -> Any:
    if _BROKER is None:
        raise RuntimeError(f"{method} is not available in this component")
    return _BROKER(method, params)


class MemoryProxy:
    async def search(self, query: str, *, top_k: int = 5) -> list:
        return list(_request("memory.search", {"query": query, "top_k": top_k}) or [])

    async def recent(self, *, limit: int = 10) -> list:
        return list(_request("memory.recent", {"limit": limit}) or [])

    def curated(self) -> str:
        return str(_request("memory.curated", {}) or "")


class FilesProxy:
    def read(self, rel: str) -> str:
        return str(_request("files.read", {"path": rel}))


class ModelProxy:
    async def complete(self, prompt: str, *, tier: str = "cheap") -> str:
        return str(_request("llm.complete", {"prompt": prompt, "tier": tier}) or "")

    async def embed(self, text: str) -> list:
        return list(_request("llm.embed", {"text": text}) or [])


class ComponentContext:
    """What a hosted component is constructed with. There is no runtime."""

    def __init__(self, options: Mapping[str, Any] | None = None, **extra: Any) -> None:
        self.api_version = str(extra.pop("api_version", "iris/v1"))
        self.kind = str(extra.pop("kind", ""))
        self.name = str(extra.pop("name", ""))
        self.options = dict(options or {})
        self.options.update(extra)
        self.memory = MemoryProxy()
        self.files = FilesProxy()
        self.llm = ModelProxy()
        self.runtime = None
