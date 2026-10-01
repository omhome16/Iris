"""Approval pauses and the stream writer, without a graph framework.

`interrupt(payload)` is what a tool calls when the owner has to decide.
The first call raises `GraphInterrupt`. A resume sets the decision first,
and the same call then *returns* that decision, so the tool continues
instead of asking again. A second interrupt in the same resume pauses again.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

_DECISION: ContextVar[str | None] = ContextVar("iris_approval_decision", default=None)
_WRITER: ContextVar[Callable[[dict], None]] = ContextVar(
    "iris_stream_writer", default=lambda _event: None
)


class GraphInterrupt(Exception):
    """The tool stopped so the owner can approve or refuse. Not a failure."""

    def __init__(self, value: dict) -> None:
        super().__init__(str(value))
        self.value = value


def interrupt(value: dict) -> Any:
    """Pause for a decision, or return the decision a resume already supplied."""
    decision = _DECISION.get()
    if decision is not None:
        _DECISION.set(None)
        return decision
    raise GraphInterrupt(value)


def set_resume_decision(decision: str | None) -> None:
    _DECISION.set(decision)


def get_stream_writer() -> Callable[[dict], None]:
    return _WRITER.get()


def set_stream_writer(writer: Callable[[dict], None]):
    return _WRITER.set(writer)


def reset_stream_writer(token) -> None:
    _WRITER.reset(token)
