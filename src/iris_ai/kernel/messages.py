"""Plain conversation messages. The turn loop stores these, not a framework's."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class RemoveMessage:
    """Drop a stored message by id when a turn compacts or finishes onboarding."""

    id: str


@dataclass
class Msg:
    type: str
    content: Any = ""
    id: str = ""
    tool_calls: list | None = None
    tool_call_id: str = ""
    name: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex


def coerce(item: Any) -> Msg | RemoveMessage:
    """Accept a Msg, a RemoveMessage, or the dict shape the nodes already return."""
    if isinstance(item, (Msg, RemoveMessage)):
        return item
    if isinstance(item, dict) and item.get("type") == "remove":
        return RemoveMessage(id=str(item.get("id") or ""))
    if not isinstance(item, dict):
        content = getattr(item, "content", "")
        kind = getattr(item, "type", "") or "ai"
        return Msg(
            type=kind,
            content=content,
            id=str(getattr(item, "id", "") or ""),
            tool_calls=getattr(item, "tool_calls", None),
            tool_call_id=str(getattr(item, "tool_call_id", "") or ""),
            name=str(getattr(item, "name", "") or ""),
        )
    if "id" in item and set(item) <= {"id"}:
        return RemoveMessage(id=str(item["id"]))
    kind = item.get("type") or ""
    role = item.get("role") or ""
    if not kind:
        kind = {"user": "human", "human": "human", "assistant": "ai", "ai": "ai", "tool": "tool"}.get(
            role, "ai"
        )
    if kind in ("assistant",):
        kind = "ai"
    if kind in ("user",):
        kind = "human"
    return Msg(
        type=kind,
        content=item.get("content", ""),
        id=str(item.get("id") or ""),
        tool_calls=item.get("tool_calls"),
        tool_call_id=str(item.get("tool_call_id") or ""),
        name=str(item.get("name") or ""),
    )


def merge_messages(existing: list, incoming: list) -> list:
    """Append new messages and apply removals. Order of `incoming` is kept."""
    out = list(existing)
    for raw in incoming:
        item = coerce(raw)
        if isinstance(item, RemoveMessage):
            out = [m for m in out if getattr(m, "id", None) != item.id]
        else:
            out.append(item)
    return out


def msg_to_dict(message: Msg) -> dict:
    return {
        "type": message.type,
        "content": message.content,
        "id": message.id,
        "tool_calls": message.tool_calls,
        "tool_call_id": message.tool_call_id,
        "name": message.name,
    }


def msg_from_dict(data: dict) -> Msg:
    return Msg(
        type=str(data.get("type") or "ai"),
        content=data.get("content", ""),
        id=str(data.get("id") or ""),
        tool_calls=data.get("tool_calls"),
        tool_call_id=str(data.get("tool_call_id") or ""),
        name=str(data.get("name") or ""),
    )
