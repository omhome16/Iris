"""Pre-tool guards — the ceilings that refuse *before* a call is made.

The audit's highest-ROI finding (G1) and its companion (G2). The vault's numbers
make the case: an uncontrolled agent loop costs roughly $2 per 30 seconds, and a
circuit-broken one about $0.01. The difference is not a better model, it is a
guard that runs **pre-dispatch** — outside the tool, outside the graph — so the
refusal costs nothing and never reaches a provider.

The chain has a fixed order. Each guard can only ever *refuse*; none of them can
re-enable what another closed:

    budget → circuit → spiral/dedup → context → record

- **budget** (`iris.budget`) — declared token ceilings, per-turn and per-day.
- **circuit** — two consecutive failures of the same tool open that tool for the
  rest of the run; three distinct tools failing in one turn escalates the turn.
  A tool that just failed twice is not a tool to call a third time.
- **spiral/dedup** — the vault's thresholds: the same tool with normalised
  arguments ≥3×, argument **Jaccard > 0.72**, or more than the declared number of
  calls in one turn when the normal is 1–3.
- **context** — a refusal carries the reason *and* what to do instead. A guard
  that answers only "no" teaches the model to retry.

Everything here is deterministic and model-free: no JEV call, no LLM call, no
network. A guard that needs a model to decide whether to spend money is a guard
that can itself run away.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from iris import turnlog
from iris.budget import Budget


class GuardName(StrEnum):
    BUDGET = "budget"
    CIRCUIT = "circuit"
    SPIRAL = "spiral"
    CONTEXT = "context"


@dataclass(frozen=True, slots=True)
class Verdict:
    """One guard's answer. `allowed` is the only thing callers read."""

    allowed: bool
    guard: str = ""
    reason: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return not self.allowed


ALLOW = Verdict(True)


# ── argument similarity ─────────────────────────────────────────────────────


def _leaves(prefix: str, value: object) -> Iterator[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield from _leaves(f"{prefix}.{key}" if prefix else str(key), item)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _leaves(f"{prefix}[{index}]", item)
    else:
        yield prefix, str(value)


def arg_tokens(args: object) -> set[str]:
    """Flatten arguments into `key=value` tokens for a set comparison.

    Values are lowercased and stripped so `{"q": " Rust "}` and `{"q": "rust"}`
    count as the same call — a loop that varies only whitespace is a loop.
    """
    if not isinstance(args, Mapping):
        return {str(args).strip().lower()} if args is not None else set()
    return {f"{path.strip().lower()}={value.strip().lower()}" for path, value in _leaves("", args)}


def arg_similarity_tokens(left: set[str], right: set[str]) -> float:
    """Jaccard over two token sets. Empty-vs-empty counts as identical."""
    union = left | right
    if not union:
        return 1.0
    return len(left & right) / len(union)


def arg_similarity(a: object, b: object) -> float:
    """Jaccard over flattened argument tokens. `1.0` is the same shape and
    values; `0.0` shares nothing."""
    return arg_similarity_tokens(arg_tokens(a), arg_tokens(b))


def normalised_args(args: object) -> str:
    """A stable string of the arguments, for the trace — never a raw dump."""
    try:
        return json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return repr(args)


# ── guard 2: the cascade breaker ────────────────────────────────────────────


class CircuitBreaker:
    """Per-run, per-tool failure memory.

    "Consecutive" is the important word: a tool that fails once and then works is
    healthy, and a tool that fails twice in a row is not going to succeed on the
    third attempt. Opening the circuit converts a retry storm into one refusal.
    """

    def __init__(self, *, failure_threshold: int = 2, failing_tools_per_turn: int = 3) -> None:
        self.failure_threshold = max(1, int(failure_threshold))
        self.failing_tools_per_turn = max(1, int(failing_tools_per_turn))
        self._consecutive: dict[str, int] = {}
        self._open: set[str] = set()
        self._failing: set[str] = set()

    @property
    def open_tools(self) -> frozenset[str]:
        return frozenset(self._open)

    @property
    def failing_tools(self) -> frozenset[str]:
        return frozenset(self._failing)

    def reset(self) -> None:
        self._consecutive.clear()
        self._open.clear()
        self._failing.clear()

    def note(self, tool: str, ok: bool) -> None:
        """Record one call's outcome. Opening the circuit is not itself a verdict."""
        if ok:
            self._consecutive.pop(tool, None)
            return
        streak = self._consecutive.get(tool, 0) + 1
        self._consecutive[tool] = streak
        self._failing.add(tool)
        if streak >= self.failure_threshold:
            self._open.add(tool)

    def check(self, tool: str) -> Verdict:
        if tool in self._open:
            return Verdict(
                False,
                GuardName.CIRCUIT,
                f"{tool!r} failed {self.failure_threshold} times in a row — it is unavailable "
                "for the rest of this turn; do not retry it",
                {"tool": tool, "open": sorted(self._open)},
            )
        if len(self._failing) >= self.failing_tools_per_turn:
            return Verdict(
                False,
                GuardName.CIRCUIT,
                f"{len(self._failing)} different tools have failed this turn — stop calling "
                "tools and answer from what you already have",
                {"failing": sorted(self._failing)},
            )
        return ALLOW


# ── guard 3: the spiral / dedup detector ────────────────────────────────────


class SpiralDetector:
    """The vault's loop thresholds, applied to one turn's call history."""

    def __init__(self, *, min_repeats: int = 3, jaccard: float = 0.72, max_calls: int = 8) -> None:
        self.min_repeats = max(2, int(min_repeats))
        self.jaccard = float(jaccard)
        self.max_calls = max(1, int(max_calls))
        self._calls: list[tuple[str, set[str]]] = []

    @property
    def calls(self) -> int:
        return len(self._calls)

    def reset(self) -> None:
        self._calls.clear()

    def note(self, tool: str, args: object) -> Verdict:
        """Register a call; refuse it if it repeats or the turn is growing."""
        # Growth first: a turn at the ceiling is stopped whatever it is calling.
        if len(self._calls) >= self.max_calls:
            return Verdict(
                False,
                GuardName.SPIRAL,
                f"{len(self._calls) + 1} tool calls in one turn (ceiling {self.max_calls}) — "
                "stop and answer with what you have",
                {"calls": len(self._calls) + 1, "ceiling": self.max_calls},
            )

        tokens = arg_tokens(args)
        similar = sum(
            1
            for name, previous in self._calls
            if name == tool and arg_similarity_tokens(previous, tokens) > self.jaccard
        )
        self._calls.append((tool, tokens))
        if similar >= self.min_repeats - 1:
            return Verdict(
                False,
                GuardName.SPIRAL,
                f"{tool!r} has been called {similar + 1} times with near-identical arguments "
                "(similarity > "
                f"{self.jaccard:g}) — that will not produce a different answer; use what you have",
                {"tool": tool, "repeats": similar + 1, "jaccard": self.jaccard},
            )
        return ALLOW


# ── the chain ───────────────────────────────────────────────────────────────


@dataclass
class GuardChain:
    """`budget → circuit → spiral` — evaluated before every dispatch.

    Constructed with settings in the engine and held for the life of the graph,
    because the day-scoped budget must outlive a turn while the turn-scoped
    detectors must not. `reset_turn()` is the only thing that clears state, and
    it clears exactly the turn-scoped half.
    """

    enabled: bool = True
    budget: Budget | None = None
    max_calls_per_turn: int = 8
    spiral_min_repeats: int = 3
    spiral_jaccard: float = 0.72
    failure_threshold: int = 2
    failing_tools_per_turn: int = 3
    _spiral: SpiralDetector = field(init=False)
    _circuit: CircuitBreaker = field(init=False)

    def __post_init__(self) -> None:
        self._spiral = SpiralDetector(
            min_repeats=self.spiral_min_repeats,
            jaccard=self.spiral_jaccard,
            max_calls=self.max_calls_per_turn,
        )
        self._circuit = CircuitBreaker(
            failure_threshold=self.failure_threshold,
            failing_tools_per_turn=self.failing_tools_per_turn,
        )

    @classmethod
    def from_settings(cls, budget: Budget | None = None) -> GuardChain:
        from iris.config import settings

        return cls(
            enabled=settings.tool_guard_enabled,
            budget=budget
            if budget is not None
            else Budget(policy=_policy_from_settings()),
            max_calls_per_turn=settings.tool_max_calls_per_turn,
            spiral_min_repeats=settings.tool_spiral_min_repeats,
            spiral_jaccard=settings.tool_spiral_jaccard,
            failure_threshold=settings.tool_failure_threshold,
            failing_tools_per_turn=settings.tool_failing_tools_per_turn,
        )

    def reset_turn(self) -> None:
        self._spiral.reset()
        self._circuit.reset()

    def end_turn(self, usage: Mapping[str, Mapping[str, int]] | None = None) -> None:
        """Retire a finished turn: roll the day if it changed, then bank the spend.

        This is what makes the day ceiling real. `Budget.refusal()` reads the
        day counters, and nothing else writes them — so without this call the
        per-day ceiling reads zero forever and the cross-session bound the vault
        asks for is a number in a config file rather than a limit.

        Called *inside* the turn's `turnlog.collect()` block, because that is the
        only moment the per-tier usage exists: the log is discarded when the
        block exits.
        """
        if self.budget is None:
            return
        self.budget.roll_day()
        if usage:
            self.budget.note_usage(usage)

    def before(self, tool: str, args: object) -> Verdict:
        """May this call proceed? The only entry point a caller needs."""
        if not self.enabled:
            return ALLOW

        if self.budget is not None:
            reason = self.budget.refusal(turn_tokens=turnlog.usage_total())
            if reason:
                return Verdict(
                    False,
                    GuardName.BUDGET,
                    reason,
                    {"turn_tokens": turnlog.usage_total(), "policy": self.budget.policy.version},
                )

        verdict = self._circuit.check(tool)
        if verdict.refused:
            return verdict

        return self._spiral.note(tool, args)

    def after(self, tool: str, ok: bool) -> None:
        """Record a call's outcome so the circuit can open."""
        if not self.enabled:
            return
        self._circuit.note(tool, ok)

    def snapshot(self) -> dict:
        """The chain's declared policy *and* its live state, for `iris guards`
        and `GET /guards`. A limit nobody can read is a limit nobody can trust.
        """
        return {
            "enabled": self.enabled,
            "order": [
                GuardName.BUDGET.value,
                GuardName.CIRCUIT.value,
                GuardName.SPIRAL.value,
                GuardName.CONTEXT.value,
                "record",
            ],
            "max_calls_per_turn": self.max_calls_per_turn,
            "spiral_min_repeats": self.spiral_min_repeats,
            "spiral_jaccard": self.spiral_jaccard,
            "failure_threshold": self.failure_threshold,
            "failing_tools_per_turn": self.failing_tools_per_turn,
            "circuit_open": sorted(self._circuit.open_tools),
            "failing_tools": sorted(self._circuit.failing_tools),
            "budget": self.budget.snapshot() if self.budget is not None else None,
        }

    def record(self, verdict: Verdict, tool: str, args: object) -> None:
        """Put the decision in the turn trace, whatever it was."""
        if verdict.allowed:
            return
        # `detail` may already carry `tool` (the circuit's open set); drop it so
        # the explicit keyword cannot collide.
        detail = {key: value for key, value in verdict.detail.items() if key != "tool"}
        turnlog.record(
            "tool_guard",
            event=str(verdict.guard),
            tool=tool,
            reason=verdict.reason,
            **detail,
        )


def _policy_from_settings():
    from iris.budget import BudgetPolicy

    return BudgetPolicy.from_settings()
