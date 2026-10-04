"""v1 component protocols, plus the v0 names authors already import."""

from __future__ import annotations

from typing import Protocol

from iris_ai.protocols import Capture, Consolidator, ContextBuilder, PersonaSource
from iris_ai.sdk.types import (
    CaptureRequest,
    ConsolidationPlan,
    ConsolidationRequest,
    ContextRequest,
    ContextResult,
    MemoryCandidate,
)

__all__ = [
    "Capture",
    "CaptureComponent",
    "Consolidator",
    "ConsolidatorComponent",
    "ContextBuilder",
    "ContextComponent",
    "PersonaComponent",
    "PersonaSource",
]


class ContextComponent(Protocol):
    async def assemble(self, request: ContextRequest) -> ContextResult: ...


class CaptureComponent(Protocol):
    async def extract(self, request: CaptureRequest) -> list[MemoryCandidate]: ...


class ConsolidatorComponent(Protocol):
    async def propose(self, request: ConsolidationRequest) -> ConsolidationPlan: ...


class PersonaComponent(Protocol):
    def text(self) -> str: ...
