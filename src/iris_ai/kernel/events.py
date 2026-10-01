"""The one stream contract: the loop, the TUI, the API and the bridge.

Each event unpacks as the legacy `(mode, payload)` pair `respond_stream`
callers already match on, and also carries a typed `kind` for new clients.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TextDelta:
    delta: str
    kind: str = "text"

    def __iter__(self):
        yield "custom"
        yield {"kind": "text", "delta": self.delta}


@dataclass(frozen=True)
class Thinking:
    delta: str
    kind: str = "thinking"

    def __iter__(self):
        yield "custom"
        yield {"kind": "thinking", "delta": self.delta}


@dataclass(frozen=True)
class ThinkingDone:
    text: str
    kind: str = "thinking_done"

    def __iter__(self):
        yield "custom"
        yield {"kind": "thinking_done", "text": self.text}


@dataclass(frozen=True)
class ToolStart:
    call: dict
    kind: str = "tool_call"

    def __iter__(self):
        yield "custom"
        yield {"kind": "tool_call", "call": self.call}


@dataclass(frozen=True)
class ToolEnd:
    name: str
    ok: bool
    summary: str
    result: str = ""
    kind: str = "tool_end"

    def __iter__(self):
        yield "custom"
        yield {
            "kind": "tool_end",
            "name": self.name,
            "ok": self.ok,
            "summary": self.summary,
            "result": self.result,
        }


@dataclass(frozen=True)
class Approval:
    payload: dict
    kind: str = "approval"

    def __iter__(self):
        yield "custom"
        yield {"kind": "approval", "payload": self.payload}


@dataclass(frozen=True)
class Status:
    text: str
    kind: str = "status"

    def __iter__(self):
        yield "custom"
        yield {"kind": "status", "text": self.text}


@dataclass(frozen=True)
class Usage:
    tokens: int = 0
    cost: float = 0.0
    model: str = ""
    kind: str = "usage"

    def __iter__(self):
        yield "custom"
        yield {"kind": "usage", "tokens": self.tokens, "cost": self.cost, "model": self.model}


@dataclass(frozen=True)
class ErrorEvent:
    text: str
    kind: str = "error"

    def __iter__(self):
        yield "error"
        yield self.text


@dataclass(frozen=True)
class Done:
    text: str = ""
    kind: str = "reply"

    def __iter__(self):
        yield "custom"
        yield {"kind": "reply", "text": self.text}


@dataclass(frozen=True)
class NodeUpdate:
    """A node finished. Legacy clients read this as an `updates` tuple."""

    node: str
    update: dict = field(default_factory=dict)
    kind: str = "update"

    def __iter__(self):
        yield "updates"
        yield {self.node: self.update}


Event = (
    TextDelta
    | Thinking
    | ThinkingDone
    | ToolStart
    | ToolEnd
    | Approval
    | Status
    | Usage
    | ErrorEvent
    | Done
    | NodeUpdate
)


def event_from_custom(payload: dict) -> Event:
    """Map a writer payload (`{"kind": "text", "delta": ...}`) onto an event."""
    kind = payload.get("kind")
    if kind == "thinking":
        return Thinking(delta=str(payload.get("delta") or ""))
    if kind == "thinking_done":
        return ThinkingDone(text=str(payload.get("text") or ""))
    if kind == "text":
        return TextDelta(delta=str(payload.get("delta") or ""))
    if kind == "tool_call":
        call = payload.get("call") or {}
        return ToolStart(call=call if isinstance(call, dict) else {})
    if kind == "approval":
        body = payload.get("payload") or {}
        return Approval(payload=body if isinstance(body, dict) else {})
    if kind == "error":
        return ErrorEvent(text=str(payload.get("text") or payload.get("delta") or ""))
    if kind == "status":
        return Status(text=str(payload.get("text") or ""))
    return Status(text=str(payload))


def legacy_pair(event: Event) -> tuple[str, Any]:
    mode, payload = event
    return mode, payload
