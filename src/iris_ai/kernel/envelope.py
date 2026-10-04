"""What happens around an engine, on every turn, for every engine."""

from __future__ import annotations

from typing import Any


def begin(state: dict, *, killed: bool) -> dict | None:
    """Envelope before the engine. A killed turn never reaches it."""
    if killed:
        return {"killed": True}
    return None


def finish(state: dict, *, engine: str, nodes: list[str]) -> dict[str, Any]:
    """Fields the trace records after the engine returns."""
    return {"engine": engine, "nodes": list(nodes)}
