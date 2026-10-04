"""What a component author imports to test a folder without booting Iris."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from iris_ai.capabilities.memory import REQUIRED
from iris_ai.plug import check_folder
from iris_ai.sdk.context import ComponentContext, FixedClock
from iris_ai.sdk.types import (
    CaptureRequest,
    ConsolidationRequest,
    ContextRequest,
)


class InMemoryChannel:
    """A channel with no socket. Tests push messages and read what was sent."""

    name = "memory"
    capabilities = frozenset({"stream", "edit", "approve"})

    def __init__(self) -> None:
        self.inbox: list = []
        self.outbox: list = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def receive(self):
        while self.inbox:
            yield self.inbox.pop(0)

    async def send(self, message) -> str:
        self.outbox.append(message)
        return str(len(self.outbox))

    def push(self, message) -> None:
        self.inbox.append(message)


def check_component(path: str | Path) -> tuple[bool, str]:
    """Import `path` and confirm it implements its kind. Safe to call from tests."""
    return check_folder(Path(path))


def fake_context(kind: str, **grants: object) -> ComponentContext:
    """A v1 context. Ungranted capabilities stay None. No runtime."""
    return ComponentContext(
        api_version="iris/v1",
        kind=kind,
        name="test",
        clock=FixedClock(date(2026, 6, 14)),
        llm=grants.get("llm"),
        memory=grants.get("memory"),
        files=grants.get("files"),
        state=grants.get("state"),
    )


def sample_request(kind: str):
    """One request of the shape `check` probes with."""
    if kind == "capture":
        return CaptureRequest(user_message="We decided to use uv.", reply="Noted.", session_id="probe")
    if kind == "consolidator":
        return ConsolidationRequest(notes="- [5] a fact (note)", curated="# MEMORY.md\n")
    return ContextRequest(message="What did we decide?", session_id="probe", origin="owner")


class HashEmbedder:
    """A deterministic stand-in for an embedding model. Offline and stable."""

    def __init__(self, dims: int = 32) -> None:
        self.dims = dims

    async def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[: self.dims]]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [await self.embed(text) for text in texts]


async def memory_conformance(backend: object) -> list[str]:
    """Names of REQUIRED methods that are missing or raise on an empty call."""
    problems: list[str] = []
    for name in REQUIRED:
        method = getattr(backend, name, None)
        if not callable(method):
            problems.append(name)
    return problems
