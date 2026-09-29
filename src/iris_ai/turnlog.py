"""Per-turn observation buffer — what the judgment layer decided, and where the time went.

Two things were invisible before this module:

1. **Judgments.** JEV answers questions the rest of Iris acts on (which memory
   won, which skill loaded, what was refused at the door), but nothing recorded
   *what it answered*. The product claim is "memory you can see and trust", and
   a trust layer you cannot inspect is a black box.
2. **Where the latency is.** A turn's total wall-clock says nothing about
   whether the tail (journal + capture) or a model call dominated it, and one
   number cannot separate *prefill* from *generation*: a p99 you cannot
   attribute to either is a p99 you cannot act on.

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
# Streamed model calls in a turn. A ReAct loop is already bounded (tool calls
# per turn, handoffs per turn), so this is a backstop rather than a budget.
_MAX_THROUGHPUT = 12
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
    # Prefill vs generation, one entry per *streamed* model call. Deliberately a
    # list and not a sum: two streamed calls had two different prefills, and
    # adding them invents a number. The buffered path has no first token to
    # measure, so it records nothing here rather than guessing a split.
    throughput: list[dict[str, float]] = field(default_factory=list)

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

    def completion_tokens(self) -> int:
        """Generated tokens this turn, across every tier."""
        return sum(int(b.get("completion_tokens", 0)) for b in self.usage.values())

    def add_throughput(
        self, *, ttft_ms: float | None, elapsed_ms: float, generated_tokens: int
    ) -> None:
        """Split one streamed call into prefill and generation.

        - `ttft_ms` — request to first token. Prefill plus queueing: the part of
          the wait that a shorter prompt or a warm cache would shrink.
        - `tpot_ms` — milliseconds per token *after* the first. Generation
          speed, which is what a bigger model or a longer answer costs.
        - `tps` — output tokens per second over the whole call, derived from the
          final usage so it is the provider's own count rather than a guess from
          the stream length.

        `ttft_ms=None` means no token ever arrived (a provider failure); the
        elapsed time is still recorded, because that wait was real.
        """
        if len(self.throughput) >= _MAX_THROUGHPUT:
            return
        tokens = max(0, int(generated_tokens or 0))
        entry: dict[str, float] = {"elapsed_ms": round(max(0.0, elapsed_ms), 1)}
        if ttft_ms is not None:
            entry["ttft_ms"] = round(max(0.0, ttft_ms), 1)
        if tokens:
            entry["output_tokens"] = tokens
            if elapsed_ms > 0:
                entry["tps"] = round(tokens / (elapsed_ms / 1000.0), 1)
            # TPOT excludes the first token on both sides: the prefill time is
            # already attributed to TTFT, and the first token is what TTFT
            # measured. Dividing by `tokens` here was the classic off-by-one
            # that makes a chat and a long generation look alike.
            if ttft_ms is not None and tokens > 1:
                generation_ms = max(0.0, elapsed_ms - ttft_ms)
                if generation_ms > 0:
                    entry["tpot_ms"] = round(generation_ms / (tokens - 1), 1)
        self.throughput.append(entry)

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
        if self.throughput:
            out["throughput"] = [dict(entry) for entry in self.throughput]
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


def completion_tokens_total() -> int:
    """Generated tokens so far this turn (0 outside a turn).

    Deltas of this around one streamed call are how the throughput split gets
    the provider's own token count: the client records usage as it arrives, and
    nothing else calls a model while the agent's stream is open.
    """
    log = _current.get()
    return log.completion_tokens() if log is not None else 0


def record_throughput(
    *, ttft_ms: float | None, elapsed_ms: float, generated_tokens: int
) -> None:
    """Record one streamed call's prefill/generation split. No-op outside a turn.

    Best-effort like every other entry point here: telemetry must never cost a
    reply, and a broken `add_throughput` is swallowed rather than raised.
    """
    log = _current.get()
    if log is None:
        return
    with contextlib.suppress(Exception):
        log.add_throughput(
            ttft_ms=ttft_ms, elapsed_ms=elapsed_ms, generated_tokens=generated_tokens
        )


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
    """Start a fresh turn log. Nested calls isolate (the inner one wins).

    The reset is guarded because a *streamed* turn can be abandoned. The CLI, an
    SSE client that disconnects, and an ACP `session/cancel` all leave
    `respond_stream`'s generator to be finalized by the event loop, and that
    finalization runs in a different context — where `ContextVar.reset(token)`
    raises `ValueError` instead of being a no-op. The value being restored
    belongs to a context that is already gone, so leaving it set is the right
    outcome; the alternative is a traceback logged for a turn nobody is reading.
    """
    log = TurnLog(id=next(_turn_seq))
    token = _current.set(log)
    try:
        yield log
    finally:
        with contextlib.suppress(ValueError):
            _current.reset(token)
