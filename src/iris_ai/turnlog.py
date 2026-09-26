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
import itertools
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

# One turn can produce a judgment per recalled chunk; cap what we keep.
_MAX_JUDGMENTS = 60
# Free-text reasons are for a human reading a trace, not a log dump.
_MAX_TEXT = 240

_current: contextvars.ContextVar[TurnLog | None] = contextvars.ContextVar("iris_turnlog", default=None)

# Monotonic per-turn id. State that must be scoped to *a turn* — the multi-agent
# allowance is the reason this exists — keys on it rather than trying to work out
# where a turn begins. `collect()` is only ever opened at a turn's entry point.
_turn_seq = itertools.count(1)


@dataclass(slots=True)
class TurnLog:
    """Judgments, stage timings and token spend for one graph run."""

    id: int = 0  # identity of this turn (see `_turn_seq`)
    judgments: list[dict] = field(default_factory=list)
    stages: dict[str, int] = field(default_factory=dict)
    dropped: int = 0
    # Which models served each tier this turn, for the trace and the `/costs`
    # style readout. Kept separate from `usage` because it is an identity, not a
    # count to add up.
    models: dict[str, list[str]] = field(default_factory=dict)
    # tier -> {calls, prompt_tokens, completion_tokens}. Per *turn*, not per
    # process: the multi-agent path costs ~15x a chat, and that has to be
    # visible where the decision was made rather than discovered in the ledger
    # at the end of the month.
    usage: dict[str, dict[str, int]] = field(default_factory=dict)
    # Which prompt policy produced this turn, and a fingerprint of the assembled
    # prefix. Set by the context assembler; empty for a turn that never
    # assembled one (a direct tool call in a test), so quiet turns stay compact.
    prompt_version: str = ""
    prompt_fingerprint: str = ""

    def add_usage(
        self,
        *,
        tier: str,
        model: str = "",
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        cached_tokens: int = 0,
    ) -> None:
        bucket = self.usage.setdefault(
            tier or "strong",
            {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0},
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += int(prompt_tokens or 0)
        bucket["completion_tokens"] += int(completion_tokens or 0)
        # Cached prompt tokens are counted where they are observed, so the day
        # budget's CACHED bucket has a source rather than being declared and
        # always zero. Providers report them differently; the LLM client is the
        # one place that knows, and it passes them through here.
        bucket["cached_tokens"] += int(cached_tokens or 0)
        if model:
            models = self.models.setdefault(tier or "strong", [])
            if model not in models:
                models.append(model)

    def total_tokens(self) -> int:
        return sum(
            int(b.get("prompt_tokens", 0)) + int(b.get("completion_tokens", 0))
            for b in self.usage.values()
        )

    def cached_tokens(self) -> int:
        """Prompt tokens served from a provider's cache this turn."""
        return sum(int(b.get("cached_tokens", 0)) for b in self.usage.values())

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
        out: dict[str, Any] = {}
        # Prompt identity rides on every assembled turn. It is deliberately
        # outside the guard below: attribution matters most on an ordinary turn,
        # which is exactly the one that would otherwise be too small to carry it.
        if self.prompt_version or self.prompt_fingerprint:
            out["prompt"] = {
                "version": self.prompt_version,
                "fingerprint": self.prompt_fingerprint,
            }
        if self.judgments or self.stages or self.usage:
            out["stages_ms"] = dict(self.stages)
            if self.usage:
                out["usage"] = {tier: dict(b) for tier, b in self.usage.items()}
                out["total_tokens"] = self.total_tokens()
                if self.models:
                    out["models"] = {tier: list(names) for tier, names in self.models.items()}
            if self.judgments:
                out["events"] = self.judgments
                # Counts let consumers summarise without re-scanning events.
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


def note_prompt(fingerprint: str) -> None:
    """Record which prompt policy and which assembled prefix produced this turn.

    Best-effort and a no-op outside a turn, like every other entry point here.
    """
    log = _current.get()
    if log is None:
        return
    from iris_ai.config import settings

    log.prompt_version = settings.prompt_version
    log.prompt_fingerprint = fingerprint


def mark(stage: str, ms: float) -> None:
    """Record a stage duration in milliseconds. No-op outside a turn."""
    log = _current.get()
    if log is None:
        return
    with contextlib.suppress(Exception):
        log.mark(stage, ms)


def add_usage(
    *,
    tier: str,
    model: str = "",
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    cached_tokens: int = 0,
) -> None:
    """Add one model call's tokens to the turn in flight. No-op outside a turn.

    Called by the LLM client's recording path, so every provider call is counted
    without any call site having to remember to report it.
    """
    log = _current.get()
    if log is None:
        return
    with contextlib.suppress(Exception):
        log.add_usage(
            tier=tier,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cached_tokens=cached_tokens,
        )


def usage_total() -> int:
    """Prompt + completion tokens spent so far this turn (0 outside a turn)."""
    log = _current.get()
    return log.total_tokens() if log is not None else 0


def usage_snapshot() -> dict[str, dict[str, int]]:
    """A copy of the per-tier usage so far (empty outside a turn)."""
    log = _current.get()
    return {tier: dict(b) for tier, b in log.usage.items()} if log is not None else {}


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


def active_id() -> int:
    """The id of the turn in flight, or 0 outside a turn."""
    log = _current.get()
    return log.id if log is not None else 0


@contextmanager
def collect() -> Iterator[TurnLog]:
    """Start a fresh turn log. Nested calls isolate (the inner one wins)."""
    log = TurnLog(id=next(_turn_seq))
    token = _current.set(log)
    try:
        yield log
    finally:
        _current.reset(token)
