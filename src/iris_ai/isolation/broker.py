"""Grants for a component host. A child authority can only shrink.

The broker runs in the parent. The child asks; this checks the grant and then
calls the handler. There is no `net.request` grant: a socket is not a
capability this host hands out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

# Methods the child asks the broker to run. In-process capabilities are built
# from this same set, so the two hosts cannot drift.
CANONICAL_GRANTS = frozenset(
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

# Short names the docs and the shipped catalog already declare. Each one is the
# in-process capability, expanded to the broker methods it turns on.
GRANT_ALIASES: dict[str, frozenset[str]] = {
    "llm": frozenset({"llm.complete", "llm.embed"}),
    "memory.read": frozenset({"memory.search", "memory.recent", "memory.curated"}),
    "files.read": frozenset({"files.read"}),
    "state": frozenset({"state.read", "state.write"}),
}

# A manifest may write either spelling. Unknown names are still refused.
KNOWN_GRANTS = CANONICAL_GRANTS | frozenset(GRANT_ALIASES)


def expand_grants(grants: Iterable[str]) -> frozenset[str]:
    """Broker methods for a declared permission list.

    A short name expands to the methods the in-process context already turns
    on. A canonical name stays itself. Anything else is refused here, which is
    the same check the subprocess authority runs before a child starts.
    """
    expanded: set[str] = set()
    unknown: list[str] = []
    for name in grants:
        if name in GRANT_ALIASES:
            expanded.update(GRANT_ALIASES[name])
        elif name in CANONICAL_GRANTS:
            expanded.add(name)
        else:
            unknown.append(name)
    if unknown:
        raise ValueError(f"unknown grant: {', '.join(sorted(unknown))}")
    return frozenset(expanded)


class Authority:
    """The grants one component is allowed to ask for."""

    def __init__(self, grants: Iterable[str], *, llm_cap: int = 4) -> None:
        # expand_grants refuses a name that is neither canonical nor an alias.
        chosen = expand_grants(grants)
        if llm_cap < 0:
            raise ValueError("llm cap cannot be negative")
        self.grants = chosen
        self.llm_cap = int(llm_cap)

    def narrow(self, grants: Iterable[str] | None = None, *, llm_cap: int | None = None) -> Authority:
        """A child gets a subset of these grants and a cap no higher than this one."""
        wanted = self.grants if grants is None else expand_grants(grants)
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
