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
_BOUND: ContextVar[dict | None] = ContextVar("iris_approval_bound", default=None)
_WRITER: ContextVar[Callable[[dict], None]] = ContextVar(
    "iris_stream_writer", default=lambda _event: None
)


class GraphInterrupt(Exception):
    """The tool stopped so the owner can approve or refuse. Not a failure."""

    def __init__(self, value: dict) -> None:
        super().__init__(str(value))
        self.value = value


def approval_pin_failure(value: dict) -> str | None:
    """Why this resume must not grant `value`, or None when it may.

    None also means no decision is waiting yet: the first `interrupt` still
    pauses. A waiting approval with no bound payload, or one whose action,
    call id, or digest differs from `value`, is refused. Tamper protection
    then does not depend on each call site remembering to compare the payload.
    """
    if _DECISION.get() is None:
        return None
    bound = _BOUND.get()
    if not isinstance(bound, dict):
        return "approval payload missing"
    if not bound.get("digest") or not value.get("digest"):
        return "approval payload missing a digest"
    for key in ("action", "call_id", "digest"):
        if bound.get(key) != value.get(key):
            return f"approval payload mismatched on {key}"
    return None


def interrupt(value: dict) -> Any:
    """Pause for a decision, or return the decision a resume already supplied.

    An "approved" resume whose bound payload is missing or does not match
    `value` returns ``"refused"`` instead of the decision.
    """
    decision = _DECISION.get()
    if decision is None:
        raise GraphInterrupt(value)
    failed = approval_pin_failure(value)
    _DECISION.set(None)
    _BOUND.set(None)
    if decision == "approved" and failed:
        return "refused"
    return decision


def set_resume_decision(decision: str | None, *, bound: dict | None = None) -> None:
    """Remember the owner's decision and the approval payload it was pinned to."""
    _DECISION.set(decision)
    _BOUND.set(bound)


def bound_approval() -> dict | None:
    """The interrupt payload this resume is allowed to honour, if one was pinned."""
    return _BOUND.get()


def get_stream_writer() -> Callable[[dict], None]:
    return _WRITER.get()


def set_stream_writer(writer: Callable[[dict], None]):
    return _WRITER.set(writer)


def reset_stream_writer(token) -> None:
    _WRITER.reset(token)
