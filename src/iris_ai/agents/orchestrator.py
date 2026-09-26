"""Orchestrator — the code-owned policy around the roles.

What lives here is deliberately *policy*, not intelligence:

- **Routing stays tool-initiated.** Nothing in this module watches a message for
  a trigger. v2 deleted regex auto-research on purpose (retrieval is a tool
  call, not a regex), and the lead decides to delegate. Constructing an
  orchestrator calls no model; a turn that never delegates pays nothing.
- **Code owns every bound and every action.** How many handoffs a turn gets,
  how long the section may take, how wide a fan-out goes, whether a revision is
  allowed, and what happens on each breach. The two JEV judgments
  (`iris_ai.jev.agents`) only supply a probability; this module compares it and acts.
- **A breach degrades, never raises.** Every refusal is a `Handoff` carrying a
  stable reason, so the lead still answers from what it has. That *is* the
  "fallback to the single-agent path" the blueprint asks for: the lead always
  ends up holding the pen.
- **Merge is deterministic and provenance-preserving.** Results are merged in
  the order they were asked for, whatever order they finished in, and an
  unsourced claim is merged marked as unsourced (never as fact).

The reason the ceilings exist at all: multi-agent systems use roughly 15x the
tokens of a chat, so an unbounded orchestrator is a cost bug with a nice name.

**Budget scope.** The allowance is per *turn*, not per process. `turn()` opens a
fresh one; a delegation outside any `turn()` gets a lazily-created budget scoped
to the calling context. (The first draft of this module held one budget on the
instance — the call cap would have been spent once, on the first turn of the
process, and every later turn would have been refused.)
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import ClassVar

from iris_ai import turnlog
from iris_ai.agent.runtime import Runtime
from iris_ai.agents.handoff import (
    REFUSED_BUDGET,
    REFUSED_DEADLINE,
    REFUSED_DISABLED,
    REFUSED_NO_ROLE,
    Handoff,
    Spend,
    render_findings,
)
from iris_ai.agents.roles import ROLES, Role, get_role
from iris_ai.agents.runner import RoleRunner
from iris_ai.config import settings
from iris_ai.jev.agents import judge_effort, judge_sufficiency

# The active turn's budget, keyed by (orchestrator, turn) so two instances in
# one context cannot read each other's allowance, and a new turn cannot inherit
# the previous turn's spend. The turn half comes from `turnlog.active_id()`,
# which every turn's entry point already establishes.
_current_budget: ContextVar[tuple[int, int, TurnBudget] | None] = ContextVar(
    "iris_agent_budget", default=None
)


def _critique_question(draft: str, findings: str) -> str:
    return (
        f"Draft answer:\n{draft}\n\n"
        f"Findings it was built from:\n{findings}\n\n"
        "Check every factual claim in the draft against the findings and report each one."
    )


@dataclass
class TurnBudget:
    """One turn's allowance for the multi-agent section.

    Mutable by design — it is spent as handoffs happen, and it also keeps the
    turn's handoffs so a later step (a sufficiency check) can see the findings
    that came back. `clock` is injectable so the deadline is testable without
    sleeping.
    """

    max_calls: int | None = None
    deadline_ms: int | None = None
    clock: Callable[[], float] = time.monotonic
    calls: int = 0
    revisions: int = 0
    started: float = field(default=0.0)
    handoffs: list[Handoff] = field(default_factory=list)

    def __post_init__(self) -> None:
        # Resolved from settings when not given, so a caller can override one
        # bound without restating the others.
        if self.max_calls is None:
            self.max_calls = settings.multi_agent_max_calls
        if self.deadline_ms is None:
            self.deadline_ms = settings.multi_agent_deadline_ms
        self.started = self.clock()

    def elapsed_ms(self) -> int:
        return int((self.clock() - self.started) * 1000)

    def remaining(self) -> int:
        return max(0, int(self.max_calls) - self.calls)

    def spend_call(self) -> None:
        self.calls += 1

    def exhausted(self) -> str:
        """The reason further handoffs are refused, or "" if there is room."""
        if self.remaining() <= 0:
            return REFUSED_BUDGET
        if self.deadline_ms is not None and self.elapsed_ms() >= self.deadline_ms:
            return REFUSED_DEADLINE
        return ""

    def may_revise(self) -> bool:
        """Exactly one revision per turn, and only if the owner allows it."""
        return bool(settings.multi_agent_revise_once) and self.revisions == 0

    def spend_revision(self) -> None:
        self.revisions += 1

    def findings_text(self) -> str:
        """The merged reports so far — what a draft can be checked against.

        Only `report` handoffs: a critique is an opinion about the draft, and
        feeding it back as evidence would let one judgment cite another.
        """
        reports = [h for h in self.handoffs if h.kind == "report"]
        return merge_handoffs(reports)[0]


class Orchestrator:
    """Routes delegations to roles under a code-owned policy."""

    #: The roles this orchestrator can actually run. Kept as a class attribute so
    #: declaring a role and being able to run it cannot drift apart.
    RUNNABLE: ClassVar[frozenset[str]] = frozenset(ROLES)

    def __init__(
        self,
        runtime: Runtime,
        *,
        roles: dict[str, Role] | None = None,
        budget: TurnBudget | None = None,
    ) -> None:
        self.runtime = runtime
        self.roles = roles if roles is not None else dict(ROLES)
        # A template, not the live allowance: a fresh TurnBudget is derived from
        # it per turn so bounds carry over without the spend carrying over too.
        self._template = budget if budget is not None else TurnBudget()
        self._seq = 0
        self._tag = id(self)

    # ── per-turn scope ───────────────────────────────────────────────────
    def _fresh_budget(self) -> TurnBudget:
        t = self._template
        return TurnBudget(max_calls=t.max_calls, deadline_ms=t.deadline_ms, clock=t.clock)

    @property
    def budget(self) -> TurnBudget:
        """This turn's allowance (created lazily, one per turn, per context).

        Keyed on the active turn's id, so a cap of two calls means two calls
        *per turn* — the only reading that makes sense. Outside a turn the id is
        0, which gives direct callers (tests, tooling) one stable allowance in
        their own context.
        """
        turn_id = turnlog.active_id()
        entry = _current_budget.get()
        if entry is not None and entry[0] == self._tag and entry[1] == turn_id:
            return entry[2]
        fresh = self._fresh_budget()
        _current_budget.set((self._tag, turn_id, fresh))
        return fresh

    @contextmanager
    def turn(self) -> Iterator[TurnBudget]:
        """Open a fresh allowance explicitly (used by tests and by callers that
        run turns outside the chat graph)."""
        fresh = self._fresh_budget()
        token = _current_budget.set((self._tag, turnlog.active_id(), fresh))
        try:
            yield fresh
        finally:
            _current_budget.reset(token)

    # ── policy ───────────────────────────────────────────────────────────
    def _refuse_reason(self, role_name: str) -> str:
        """Why this delegation cannot happen, or "" if it can.

        Order is deliberate: the master switch first (it is the cheapest and the
        most explicit answer), then the role, then the budget.
        """
        if not settings.multi_agent_enabled:
            return REFUSED_DISABLED
        if role_name not in self.roles:
            return REFUSED_NO_ROLE
        return self.budget.exhausted()

    def _refusal(self, role_name: str, question: str, reason: str) -> Handoff:
        handoff = Handoff(
            id=self._next_id(role_name),
            from_role="lead",
            to_role=role_name,
            kind="request",
            question=question,
            spend=Spend(ms=0),
            refused=reason,
        )
        self._record(handoff)
        self.budget.handoffs.append(handoff)
        return handoff

    def _next_id(self, role_name: str) -> str:
        self._seq += 1
        return f"{role_name}-{self._seq}"

    def _record(self, handoff: Handoff) -> None:
        """Best-effort telemetry. A payload bug must never cost a reply."""
        with contextlib.suppress(Exception):
            turnlog.record("handoff", **handoff.trace_summary())

    # ── delegation ───────────────────────────────────────────────────────
    async def delegate(self, role_name: str, question: str, *, session_id: str = "") -> Handoff:
        """Hand one question to one role, or refuse with a stable reason."""
        reason = self._refuse_reason(role_name)
        if reason:
            return self._refusal(role_name, question, reason)
        self.budget.spend_call()
        return await self._run(role_name, question, session_id=session_id)

    async def fan_out(
        self, role_name: str, questions: Sequence[str], *, session_id: str = ""
    ) -> list[Handoff]:
        """Hand several sub-questions to one role concurrently.

        Where the documented latency win comes from, so it is a real fan-out
        (`gather`), not a loop. But **the effort judgment gates the width**: the
        named failure mode of production multi-agent systems is a swarm of
        subagents for a simple query, so a question that is not judged
        multi-part gets exactly one call. With JEV unavailable the judgment is
        empty and the width collapses to one — the pre-P5 behaviour, which is
        what "fail open" means here.

        Width is additionally capped by config and by what is left of the
        budget; results come back in the order asked for, which is what makes the
        merge deterministic. If nothing can run, a single refusal is returned
        rather than an empty list — silence and "I could not" must not look the
        same to the lead.
        """
        wanted = [q for q in questions if q.strip()]
        if not wanted:
            return []

        judgment = await judge_effort(
            getattr(self.runtime, "jev", None), question="\n".join(wanted)
        )
        width = 1
        if judgment.fan_out:
            width = min(
                len(wanted),
                max(1, int(settings.multi_agent_max_parallel)),
                self.budget.remaining(),
            )
        if width <= 0:
            reason = self._refuse_reason(role_name)
            return [self._refusal(role_name, wanted[0], reason or REFUSED_BUDGET)]

        # Charge the whole fan-out up front so concurrent calls cannot each
        # believe they are the last one within budget.
        for _ in range(width):
            self.budget.spend_call()
        return list(
            await asyncio.gather(
                *(self._run(role_name, q, session_id=session_id) for q in wanted[:width])
            )
        )

    async def verify(
        self, draft: str, *, findings: str | None = None, session_id: str = ""
    ) -> tuple[Handoff, bool]:
        """Check a draft against the turn's findings. Returns (critique, revise?).

        The judgment runs **first and alone**, because it is one cheap request
        with no subagent: a draft it is confident about needs no critic
        invocation at all. Only when the draft is not confidently grounded (or
        when JEV is unavailable, so the judgment cannot say) does the critic run
        to produce the per-claim detail — which is exactly the case where that
        detail is worth paying for.

        `revise=True` at most once per turn: after that the lead must say what it
        could not ground rather than keep rewriting.
        """
        reason = self._refuse_reason("critic")
        if reason:
            return self._refusal("critic", draft, reason), False

        budget = self.budget
        evidence = budget.findings_text() if findings is None else findings
        judgment = await judge_sufficiency(
            getattr(self.runtime, "jev", None), draft=draft, findings=evidence
        )
        gate = settings.multi_agent_critique_gate

        if judgment.screened and not judgment.below_gate:
            # Grounded enough to assert. No subagent, no call spent.
            handoff = Handoff(
                id=self._next_id("critic"),
                from_role="critic",
                to_role="lead",
                kind="critique",
                verdict=judgment.verdict,
                score=judgment.grounded,
                gate=gate,
                spend=Spend(ms=0),
            )
            self._record(handoff)
            budget.handoffs.append(handoff)
            return handoff, False

        budget.spend_call()
        handoff = await self._run(
            "critic", _critique_question(draft, evidence), session_id=session_id
        )
        handoff = replace(
            handoff,
            kind="critique",
            verdict=judgment.verdict or "partial",
            score=judgment.grounded if judgment.screened else None,
            gate=gate if judgment.screened else None,
        )
        budget.handoffs.append(handoff)
        needs_revision = budget.may_revise()
        if needs_revision:
            budget.spend_revision()
        return handoff, needs_revision

    async def _run(self, role_name: str, question: str, *, session_id: str) -> Handoff:
        """Run a role. Assumes policy already allowed (and charged) it."""
        runner = RoleRunner(self.runtime, get_role(role_name))
        handoff = await runner.run(question, session_id=session_id)
        self.budget.handoffs.append(handoff)
        return handoff

    def merge(self, handoffs: Sequence[Handoff]) -> tuple[str, bool]:
        return merge_handoffs(handoffs)


def merge_handoffs(handoffs: Sequence[Handoff]) -> tuple[str, bool]:
    """The lead-facing block for a set of handoffs.

    Deterministic: same handoffs in, same text out. Refusals are dropped (a
    refusal is not a finding), unsourced claims keep their marker, and the whole
    block is capped at the per-role cap times the call cap — derived rather than
    a second knob, so the two cannot disagree.
    """
    reports = [h for h in handoffs if not h.refused and h.claims]
    if not reports:
        return "no findings from any specialist this turn.", False

    per_role = int(settings.multi_agent_max_output_chars)
    cap = per_role * max(1, int(settings.multi_agent_max_calls))
    parts = [render_findings(h, max_chars=per_role)[0] for h in reports]
    body = "\n\n".join(parts)

    unsourced = sum(len(h.unsourced) for h in reports)
    truncated = False
    if len(body) > cap:
        clipped = body[:cap]
        if "\n" in clipped:
            clipped = clipped[: clipped.rfind("\n")]
        body, truncated = clipped, True

    with contextlib.suppress(Exception):
        turnlog.record(
            "merge",
            reports=len(reports),
            unsourced=unsourced,
            chars=len(body),
            truncated=truncated,
        )
    return body, truncated
