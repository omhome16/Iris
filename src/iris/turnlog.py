"""Per-turn observation buffer — what the judgment layer decided, and where the time went.

Two things were invisible before this module:

1. **Judgments.** JEV answers questions the rest of Iris acts on (which memory
   won, which skill loaded, what was refused at the door), but nothing recorded
   *what it answered*. The product claim is "memory you can see and trust", and
   a trust layer you cannot inspect is a black box.
2. **Where the latency is.** A turn's total wall-clock says nothing about
   whether the tail (journal + capture) or a model call dominated it.

Both are per-turn and scoped to the graph run, so they live in a ContextVar
rather than in LangGraph state: nodes inside one `ainvoke` share the context,
and nothing needs a reducer to merge.

Design rules:

- **Recording never raises and never blocks.** Every entry point is best-effort;
  a bug in telemetry must not cost a reply.
- **Bounded.** `_MAX_JUDGMENTS` entries per turn — a 30-tool-call turn cannot
  grow the trace without limit.
- **Outside a turn, everything is a no-op**, so tools can call `record()` freely
  (a tool invoked directly from a test records nothing rather than erroring).
"""

from __future__ import annotations

import contextlib
import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

# One turn can produce a judgment per recalled chunk; cap what we keep.
_MAX_JUDGMENTS = 60
# Free-text reasons are for a human reading the dashboard, not a log dump.
_MAX_TEXT = 240

_current: contextvars.ContextVar[TurnLog | None] = contextvars.ContextVar("iris_turnlog", default=None)


@dataclass(slots=True)
class TurnLog:
    """Judgments and stage timings for one graph run."""

    judgments: list[dict] = field(default_factory=list)
    stages: dict[str, int] = field(default_factory=dict)
    dropped: int = 0

    def add(self, kind: str, **fields: Any) -> None:
        if len(self.judgments) >= _MAX_JUDGMENTS:
            self.dropped += 1
            return
        entry: dict[str, Any] = {"kind": kind}
        for key, value in fields.items():
            if isinstance(value, str):
                entry[key] = value[:_MAX_TEXT]
            elif isinstance(value, float):
                entry[key] = round(value, 3)
            else:
                entry[key] = value
        self.judgments.append(entry)

    def mark(self, stage: str, ms: float) -> None:
        """Record a stage duration. Repeat visits accumulate (a ReAct loop can
        run `tools` many times), which is what makes totals add up."""
        self.stages[stage] = self.stages.get(stage, 0) + int(max(0.0, ms))

    def to_trace(self) -> dict:
        """The `judgment` block of a trace line. Omitted entirely when empty,
        so an all-deterministic turn stays a compact line."""
        if not self.judgments and not self.stages:
            return {}
        out: dict[str, Any] = {"stages_ms": dict(self.stages)}
        if self.judgments:
            out["events"] = self.judgments
            # Counts let the dashboard summarise without re-scanning events.
            counts: dict[str, int] = {}
            for entry in self.judgments:
                counts[entry["kind"]] = counts.get(entry["kind"], 0) + 1
            out["counts"] = counts
        if self.dropped:
            out["dropped"] = self.dropped
        return out


def active() -> bool:
    return _current.get() is not None


def record(kind: str, **fields: Any) -> None:
    """Record one judgment for the turn in flight. No-op outside a turn."""
    log = _current.get()
    if log is None:
        return
    # Telemetry must never break a turn: a bug here is swallowed, not raised.
    with contextlib.suppress(Exception):
        log.add(kind, **fields)


def mark(stage: str, ms: float) -> None:
    """Record a stage duration in milliseconds. No-op outside a turn."""
    log = _current.get()
    if log is None:
        return
    with contextlib.suppress(Exception):
        log.mark(stage, ms)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Time a block into the turn's stage table.

    Nested/repeated blocks accumulate, so wrapping the whole `tools` node still
    totals correctly across a multi-step ReAct loop.
    """
    started = time.monotonic()
    try:
        yield
    finally:
        mark(name, (time.monotonic() - started) * 1000)


async def stream_stage(name: str, source):
    """Time an async stream end to end, yielding its items unchanged.

    The streamed agent node is the one stage whose duration cannot be captured
    by a `with` block — it is consumed by the caller's `async for`, and on the
    streaming path it is what the owner is actually waiting on.
    """
    started = time.monotonic()
    try:
        async for item in source:
            yield item
    finally:
        mark(name, (time.monotonic() - started) * 1000)


@contextmanager
def collect() -> Iterator[TurnLog]:
    """Start a fresh turn log. Nested calls isolate (the inner one wins)."""
    log = TurnLog()
    token = _current.set(log)
    try:
        yield log
    finally:
        _current.reset(token)
