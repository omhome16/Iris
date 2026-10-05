"""Grants for a component host. A child authority can only shrink.

The broker runs in the parent. The child asks; this checks the grant and then
calls the handler. There is no `net.request` grant: a socket is not a
capability this host hands out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

KNOWN_GRANTS = frozenset(
    {
        "llm.complete",
        "llm.embed",
        "memory.search",
        "memory.recent",
        "memory.curated",
        "files.read",
        "state.read",
        "state.write",
        "log",
    }
)


class Authority:
    """The grants one component is allowed to ask for."""

    def __init__(self, grants: Iterable[str], *, llm_cap: int = 4) -> None:
        chosen = frozenset(grants)
        unknown = chosen - KNOWN_GRANTS
        if unknown:
            raise ValueError(f"unknown grant: {', '.join(sorted(unknown))}")
        if llm_cap < 0:
            raise ValueError("llm cap cannot be negative")
        self.grants = chosen
        self.llm_cap = int(llm_cap)

    def narrow(self, grants: Iterable[str] | None = None, *, llm_cap: int | None = None) -> Authority:
        """A child gets a subset of these grants and a cap no higher than this one."""
        wanted = self.grants if grants is None else frozenset(grants)
        extra = wanted - self.grants
        if extra:
            raise PermissionError(f"cannot add grants: {', '.join(sorted(extra))}")
        cap = self.llm_cap if llm_cap is None else int(llm_cap)
        if cap > self.llm_cap:
            raise PermissionError("cannot raise the model cap")
        if cap < 0:
            raise ValueError("llm cap cannot be negative")
        return Authority(wanted, llm_cap=cap)

    def allows(self, method: str) -> bool:
        return method in self.grants


class CapabilityBroker:
    """Dispatch one child request. Missing grants and missing handlers both refuse."""

    def __init__(self, authority: Authority, handlers: dict[str, Callable[[dict], Any]]) -> None:
        self.authority = authority
        self.handlers = dict(handlers)
        self.llm_used = 0

    def dispatch(self, method: str, params: dict | None = None) -> Any:
        if method not in self.authority.grants:
            raise PermissionError(f"{method} is not granted")
        handler = self.handlers.get(method)
        if handler is None:
            raise PermissionError(f"{method} has no handler")
        if method.startswith("llm."):
            if self.llm_used >= self.authority.llm_cap:
                raise RuntimeError(f"llm cap of {self.authority.llm_cap} calls reached")
            self.llm_used += 1
        return handler(dict(params or {}))
