"""What a component author imports. v1 is the documented surface.

`check_folder` stays in `iris_ai.plug` (the jailed child lives there). It is
re-exported lazily so importing this package does not import the loader while
the loader is still starting.
"""

from __future__ import annotations

from iris_ai.protocols import Capture, Consolidator, ContextBuilder, PersonaSource
from iris_ai.sdk.context import (
    Clock,
    ComponentContext,
    FixedClock,
    MemoryAccess,
    ModelAccess,
    StateDir,
    WorkspaceAccess,
)
from iris_ai.sdk.protocols import (
    CaptureComponent,
    ConsolidatorComponent,
    ContextComponent,
    PersonaComponent,
)
from iris_ai.sdk.types import (
    CaptureRequest,
    Conflict,
    ConsolidationPlan,
    ConsolidationRequest,
    ContextBlock,
    ContextRequest,
    ContextResult,
    MemoryCandidate,
    MemoryItem,
)

__all__ = [
    "Capture",
    "CaptureComponent",
    "CaptureRequest",
    "Clock",
    "ComponentContext",
    "Conflict",
    "ConsolidationPlan",
    "ConsolidationRequest",
    "Consolidator",
    "ConsolidatorComponent",
    "ContextBlock",
    "ContextBuilder",
    "ContextComponent",
    "ContextRequest",
    "ContextResult",
    "FixedClock",
    "MemoryAccess",
    "MemoryCandidate",
    "MemoryItem",
    "ModelAccess",
    "PersonaComponent",
    "PersonaSource",
    "StateDir",
    "WorkspaceAccess",
    "check_folder",
]


def __getattr__(name: str):
    if name == "check_folder":
        from iris_ai.plug import check_folder

        return check_folder
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
