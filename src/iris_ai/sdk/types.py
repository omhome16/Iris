"""Frozen, JSON-serializable values a component may return.

These types are the v1 contract. They stay free of the runtime so a later
process boundary can carry them as JSON.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


def _date(value: date | datetime | str | None) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        return date.fromisoformat(value[:10])
    return date.today()


def _pairs(value: Mapping[str, float] | list | tuple | None) -> tuple[tuple[str, float], ...]:
    if not value:
        return ()
    if isinstance(value, Mapping):
        return tuple((str(key), float(item)) for key, item in value.items())
    return tuple((str(key), float(item)) for key, item in value)


@dataclass(frozen=True, slots=True)
class ContextBlock:
    title: str
    text: str
    source: str = ""
    kind: str = "memory"
    priority: int = 50

    def render(self) -> str:
        """An empty title is the text alone, so MinimalContext stays one line."""
        if not self.title:
            return self.text
        return f"## {self.title}\n{self.text}"


@dataclass(frozen=True, slots=True)
class ContextResult:
    blocks: tuple[ContextBlock, ...] = ()
    skills: tuple[str, ...] = ()

    def render(self) -> str:
        """The prompt prefix. Joined the same way the default assembler always has."""
        return "\n\n".join(block.render() for block in self.blocks if block.text or block.title)


@dataclass(frozen=True, slots=True)
class ContextRequest:
    message: str
    session_id: str
    origin: str = "owner"
    prior: ContextResult = field(default_factory=ContextResult)


@dataclass(frozen=True, slots=True)
class MemoryItem:
    id: str
    content: str
    path: str
    observed_at: date
    origin: str
    importance: float = 0.0
    score: float = 0.0
    superseded: bool = False
    why: tuple[tuple[str, float], ...] = ()

    def why_map(self) -> dict[str, float]:
        return dict(self.why)


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    content: str
    kind: str = "fact"
    importance: float = 5.0
    supersedes: str = ""
    reason: str = ""


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    user_message: str
    reply: str
    session_id: str = ""
    context: str = ""
    prior: tuple[MemoryCandidate, ...] = ()


@dataclass(frozen=True, slots=True)
class Conflict:
    existing: MemoryItem
    incoming: MemoryCandidate
    reason: str


@dataclass(frozen=True, slots=True)
class ConsolidationPlan:
    add: tuple[MemoryCandidate, ...] = ()
    supersede: tuple[str, ...] = ()
    conflicts: tuple[Conflict, ...] = ()
    consume: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConsolidationRequest:
    notes: str = ""
    curated: str = ""
    prior: ConsolidationPlan = field(default_factory=ConsolidationPlan)


def to_jsonable(value: Any) -> Any:
    """A JSON-ready structure. Dates become ISO strings."""
    if hasattr(value, "__dataclass_fields__"):
        return {key: to_jsonable(getattr(value, key)) for key in value.__dataclass_fields__}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    return value


def dumps(value: Any) -> str:
    return json.dumps(to_jsonable(value), ensure_ascii=False)


def context_result_from(data: Mapping[str, Any]) -> ContextResult:
    blocks = tuple(
        ContextBlock(
            title=str(row.get("title", "")),
            text=str(row.get("text", "")),
            source=str(row.get("source", "")),
            kind=str(row.get("kind", "memory")),
            priority=int(row.get("priority", 50)),
        )
        for row in data.get("blocks") or []
    )
    return ContextResult(blocks=blocks, skills=tuple(data.get("skills") or ()))


def memory_item_from(data: Mapping[str, Any]) -> MemoryItem:
    why = data.get("why") or ()
    return MemoryItem(
        id=str(data.get("id", "")),
        content=str(data.get("content", "")),
        path=str(data.get("path", "")),
        observed_at=_date(data.get("observed_at")),
        origin=str(data.get("origin", "")),
        importance=float(data.get("importance") or 0),
        score=float(data.get("score") or 0),
        superseded=bool(data.get("superseded")),
        why=_pairs(why),
    )


def memory_candidate_from(data: Mapping[str, Any]) -> MemoryCandidate:
    return MemoryCandidate(
        content=str(data.get("content", "")),
        kind=str(data.get("kind", "fact")),
        importance=float(data.get("importance") if data.get("importance") is not None else 5),
        supersedes=str(data.get("supersedes", "")),
        reason=str(data.get("reason", "")),
    )


def conflict_from(data: Mapping[str, Any]) -> Conflict:
    return Conflict(
        existing=memory_item_from(data.get("existing") or {}),
        incoming=memory_candidate_from(data.get("incoming") or {}),
        reason=str(data.get("reason", "")),
    )


def plan_from(data: Mapping[str, Any]) -> ConsolidationPlan:
    return ConsolidationPlan(
        add=tuple(memory_candidate_from(row) for row in data.get("add") or []),
        supersede=tuple(str(item) for item in data.get("supersede") or []),
        conflicts=tuple(conflict_from(row) for row in data.get("conflicts") or []),
        consume=tuple(str(item) for item in data.get("consume") or []),
    )
