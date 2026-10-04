"""The engine protocol. An engine decides. The kernel acts."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

TOOLS = "__tools__"
FINISH = "__finish__"

SCRATCH_LIMIT = 16_000


@dataclass(frozen=True)
class Step:
    """One engine decision.

    `update` is merged into the turn (messages to append, plus `engine` scratch).
    `next` is `TOOLS`, `FINISH`, or another node name on this engine.
    `after` is the node to run once the kernel finishes a tool round.
    """

    update: dict
    next: str
    after: str = ""


class Engine(Protocol):
    name: str
    entry: str
    nodes: Mapping[str, Callable[..., Awaitable[Step]]]


def check_step(step: Step, *, nodes: set[str], issued: set[str], scratch: str = "") -> None:
    """Refuse a step the kernel cannot carry out."""
    if step.next not in {TOOLS, FINISH, *nodes}:
        raise ValueError(f"unknown next {step.next!r}")
    messages = step.update.get("messages") or []
    if step.next == TOOLS:
        if not messages:
            raise ValueError("TOOLS requires an assistant message")
        last = messages[-1]
        calls = last.get("tool_calls") if isinstance(last, dict) else getattr(last, "tool_calls", None)
        if not calls:
            raise ValueError("TOOLS requires tool calls")
        for call in calls:
            call_id = call.get("id") if isinstance(call, dict) else getattr(call, "id", "")
            if call_id not in issued:
                raise ValueError(f"tool call id {call_id!r} was not minted by the kernel")
    blob = step.update.get("engine")
    if blob is not None:
        import json

        try:
            encoded = json.dumps(blob)
        except TypeError as exc:
            raise ValueError("engine scratch must be JSON-serializable") from exc
        if len(encoded) > SCRATCH_LIMIT:
            raise ValueError("engine scratch exceeds the size cap")
    if scratch and len(scratch) > SCRATCH_LIMIT:
        raise ValueError("engine scratch exceeds the size cap")
