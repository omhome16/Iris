"""Judge capability — the `Judge` Protocol and its registry.

A *judgment* is a typed question about supplied text: rerank these candidates,
is this worth capturing, does this contain instructions aimed at the model, is
this draft grounded. Judgments are cheap, batched where possible, and **always
have a deterministic fallback** — a judgment layer that can itself run away is
not a safety layer.

The core implementation is `JevClient` (TypeSafe System One), registered as
``jev``. Its `enabled` flag is the whole optionality story: with no key every
call site gets ``None`` and takes its deterministic path, so a rules judge or a
local-model judge is a drop-in alternative, not a fork.

A judgment that needs *generation* is not a judgment and stays with the model.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from iris_ai.jev.client import JevClient
from iris_ai.registry import Registry

REQUIRED: tuple[str, ...] = (
    "ask",
    "close",
    "status",
    "unavailable_reason",
)


@runtime_checkable
class Judge(Protocol):
    """Everything the kernel may ask of a judgment layer."""

    @property
    def enabled(self) -> bool: ...

    def unavailable_reason(self) -> str: ...

    async def ask(self, state: Any, questions: Any) -> Any: ...

    def status(self) -> dict[str, Any]: ...

    async def close(self) -> None: ...


#: The judgment registry. `jev` is core; a rules/local judge registers here.
JUDGES: Registry[Judge] = Registry("judge")

JUDGES.register("jev", lambda **kw: JevClient(**kw), source="core")


def discover_judges(enabled: set[str] | None = None) -> list[str]:
    """Register installed judges; returns the names that were added."""
    return [reg.name for reg in JUDGES.discover("iris_ai.judges", enabled=enabled)]
