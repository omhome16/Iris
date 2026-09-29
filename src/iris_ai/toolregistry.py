"""Tool providers — the plug-and-play seam for adding tools.

Core's own toolset is the first provider (name ``core``). An installed package
can contribute more by exposing an object with ``name`` and
``tools(runtime) -> list[Tool]`` under the ``iris_ai.tools`` entry-point group;
enabling it is configuration, never a core edit.

The validation boundary is deliberate: only the **core** provider's tools are
checked against ``TOOL_NAMES`` (the declared skill-manifest surface, which must
be closed so a skill cannot name a tool that does not exist). A plugin's tools
are additive by definition and are logged instead — so "a core tool nobody
declared" (a bug) stays distinguishable from "a plugin tool" (the point).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from iris_ai.registry import Registry

if TYPE_CHECKING:
    from iris_ai.agent.runtime import Runtime
    from iris_ai.agent.tools import Tool

log = logging.getLogger("iris.tools")


@runtime_checkable
class ToolProvider(Protocol):
    """Anything that can contribute tools to the agent's surface."""

    name: str

    def tools(self, runtime: Runtime) -> list[Tool]:  # pragma: no cover - protocol
        ...


#: Registered providers. `core` is registered by `iris_ai.agent.tools` itself.
TOOL_PROVIDERS: Registry[ToolProvider] = Registry("tool_provider")


def discover_tool_providers() -> list[str]:
    """Register installed tool plugins; returns the names that were added."""
    return [reg.name for reg in TOOL_PROVIDERS.discover("iris_ai.tools")]
