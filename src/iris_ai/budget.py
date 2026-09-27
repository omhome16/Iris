"""Budgets — declared ceilings with counters split by kind, not one total.

The audit's G3 finding: budgets were per-turn only, and a single number. The
vault's position (Cheat Sheets → budgets; Agent Control) is stronger on both
axes:

- **Scopes.** A per-turn ceiling bounds a loop; it does not bound a day. A
  cross-session ceiling is what stops a runaway that spends a little every turn.
- **Split counters.** Input, output, cached, embedding and tool-schema tokens
  fail differently — cached tokens are cheap, tool-schema tokens are pure
  overhead, output tokens are the ones that run away in a loop. One total hides
  which one moved.

Scope discipline here is the same one P5's `TurnBudget` learned: state is keyed
to the thing it scopes. Turn counters reset when a turn starts; day counters
reset when the date changes and are persisted to `config/budget.json`, so the
day ceiling survives a restart rather than being a number that resets whenever
someone redeploys.

`0` means "no ceiling", everywhere — a limit nobody set is not a limit of zero.

This module imports nothing from `iris` at module scope: it is data plus
arithmetic, and the one settings read is lazy, so it can be tested directly.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from pathlib import Path

log = logging.getLogger("iris")

#: Bumped when the *meaning* of a counter or a ceiling changes. The version is
#: recorded in the snapshot and in the trace so a number can be attributed to
#: the policy that produced it rather than to "the budget".
BUDGET_POLICY_VERSION = "2026-09-25.1"


class CounterKind(StrEnum):
    """What the tokens were spent on. The split is the point."""

    INPUT = "input"
    OUTPUT = "output"
    CACHED = "cached"
    EMBEDDING = "embedding"
    #: **Reserved, and said so rather than implied.** Tool-schema tokens are
    #: real spend, but no provider reports them separately: they arrive inside
    #: `prompt_tokens`. The bucket exists so a future provider that splits them
    #: has somewhere to land, and it is zero today — see `docs/support.md`.
    TOOL_SCHEMA = "tool_schema"


def _empty_counters() -> dict[str, int]:
    return {kind.value: 0 for kind in CounterKind}


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """The declared ceilings, versioned so a measurement can name its policy."""

    version: str = BUDGET_POLICY_VERSION
    max_tokens_per_turn: int = 0
    max_tokens_per_day: int = 0

    @classmethod
    def from_settings(cls) -> BudgetPolicy:
        from iris_ai.config import settings

        return cls(
            version=settings.budget_policy_version,
            max_tokens_per_turn=settings.budget_max_tokens_per_turn,
            max_tokens_per_day=settings.budget_max_tokens_per_day,
        )

    @property
    def enforced(self) -> bool:
        return bool(self.max_tokens_per_turn or self.max_tokens_per_day)


@dataclass
class Budget:
    """Day-scoped split counters + a policy. Turn scope is read, not stored.

    Turn tokens are read from the live turn log at refusal time (the same source
    the trace reports), so there is one accounting path rather than two that can
    disagree. Day counters are owned here because nothing else survives a turn.
    """

    policy: BudgetPolicy = field(default_factory=BudgetPolicy)
    path: Path | None = None
    today: str = ""
    counters: dict[str, int] = field(default_factory=_empty_counters)
    #: Set when the day file could not be written, so `snapshot()` can say so
    #: rather than silently reporting a ceiling that will not survive a restart.
    persistence_error: str = ""

    def __post_init__(self) -> None:
        if not self.today:
            self.today = date.today().isoformat()
        self._load()

    # ── day scope ──────────────────────────────────────────────────────

    def roll_day(self, today: str | None = None) -> bool:
        """Reset if the date changed. Returns True when a roll happened."""
        current = today or date.today().isoformat()
        if current == self.today:
            return False
        self.today = current
        self.counters = _empty_counters()
        self.persistence_error = ""
        self.save()
        return True

    def total(self) -> int:
        return sum(self.counters.values())

    def note_tokens(self, kind: CounterKind | str, amount: int, *, persist: bool = True) -> None:
        if amount <= 0:
            return
        key = kind.value if isinstance(kind, CounterKind) else str(kind)
        self.counters[key] = self.counters.get(key, 0) + int(amount)
        if persist:
            self.save()

    def note_usage(self, usage: Mapping[str, Mapping[str, int]]) -> None:
        """Add one turn's per-tier usage to the day counters.

        `usage` is `turnlog`'s shape (`tier → {calls, prompt_tokens,
        completion_tokens, cached_tokens}`). The embedding tier is counted as
        embeddings rather than as conversation input — the vault's whole point
        about splitting — and cached tokens are read from the same bucket they
        were observed in, so there is one source for each number.
        """
        for tier, bucket in (usage or {}).items():
            prompt = int(bucket.get("prompt_tokens", 0) or 0)
            completion = int(bucket.get("completion_tokens", 0) or 0)
            cached = int(bucket.get("cached_tokens", 0) or 0)
            if tier == CounterKind.EMBEDDING.value:
                self.note_tokens(CounterKind.EMBEDDING, prompt + completion, persist=False)
                continue
            self.note_tokens(CounterKind.INPUT, prompt, persist=False)
            self.note_tokens(CounterKind.OUTPUT, completion, persist=False)
            self.note_tokens(CounterKind.CACHED, cached, persist=False)
        self.save()

    # ── enforcement ────────────────────────────────────────────────────

    def refusal(self, *, turn_tokens: int = 0) -> str:
        """Why further tool calls are refused, or `""` when there is room.

        Turn scope first: a runaway turn should be stopped by the ceiling that
        describes *it*, and the message should say which ceiling fired.
        """
        policy = self.policy
        if policy.max_tokens_per_turn and turn_tokens >= policy.max_tokens_per_turn:
            return (
                f"this turn has spent {turn_tokens} tokens, past the per-turn ceiling "
                f"of {policy.max_tokens_per_turn} — answer with what you have"
            )
        total = self.total()
        if policy.max_tokens_per_day and total >= policy.max_tokens_per_day:
            return (
                f"today's token ceiling ({policy.max_tokens_per_day}) is spent ({total}) — "
                "tool calls are paused until tomorrow"
            )
        return ""

    # ── the readout ────────────────────────────────────────────────────

    def snapshot(self) -> dict:
        return {
            "policy": {
                "version": self.policy.version,
                "max_tokens_per_turn": self.policy.max_tokens_per_turn,
                "max_tokens_per_day": self.policy.max_tokens_per_day,
            },
            "day": self.today,
            "counters": dict(self.counters),
            "total": self.total(),
            "persistence_error": self.persistence_error,
        }

    # ── persistence (cross-session) ────────────────────────────────────

    def _load(self) -> None:
        if self.path is None or not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.persistence_error = str(exc)
            return
        if not isinstance(data, dict) or data.get("date") != self.today:
            return  # yesterday's spend is not today's
        stored = data.get("counters")
        if isinstance(stored, dict):
            for key, value in stored.items():
                self.counters[key] = int(value)

    def save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps({"date": self.today, "counters": self.counters}, ensure_ascii=False),
                encoding="utf-8",
            )
            self.persistence_error = ""
        except OSError as exc:
            # A budget that cannot persist is still enforced in memory; saying so
            # beats a ceiling that silently resets on restart.
            self.persistence_error = str(exc)
            log.warning("could not persist the budget file: %s", exc)
