"""P5 — the orchestrator policy: what code owns, and how it degrades.

Three things are being pinned here:

1. **Routing stays tool-initiated.** v2 deliberately removed regex-triggered
   auto-research. Constructing an orchestrator must cost nothing and call no
   model; a turn that never delegates runs zero subagents.
2. **Every breach degrades, never raises.** A disabled switch, an unknown role,
   an exhausted call cap and a blown deadline all come back as a refused handoff
   with a stable reason, so the lead still answers from what it has.
3. **Merge is deterministic.** Fan-out results come back in the order they were
   asked for, whatever order they finished in, and an unsourced claim is never
   merged as if it were grounded.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.agents.handoff import (
    REFUSED_BUDGET,
    REFUSED_DEADLINE,
    REFUSED_DISABLED,
    REFUSED_NO_ROLE,
    Claim,
    Handoff,
    Source,
    Spend,
)
from iris_ai.agents.orchestrator import Orchestrator, TurnBudget
from iris_ai.agents.roles import CRITIC, RESEARCHER
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.llm import LLMClient
from test_agent_graph import make_runtime


class ReportingLLM(LLMClient):
    """One round: report. No tool calls, so the run ends immediately."""

    def __init__(self, text: str = "Report: the beach trip was in August.") -> None:
        self.calls = 0
        self.text = text
        self.tiers: list[str] = []

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        self.tiers.append(str(kwargs.get("tier", "")))
        return self.text, [], ""


class SearchThenReportLLM(LLMClient):
    """Round 1 retrieves (which is what gives a run provenance), round 2 reports."""

    def __init__(self, text: str = "Report: the beach trip was in August.") -> None:
        self.calls = 0
        self.text = text

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "memory_search", "args": {"query": "beach trip", "top_k": 5}}], ""
        return self.text, [], ""


class FakeJev:
    """Canned judgments, so the orchestration policy is testable without JEV."""

    def __init__(self, *, multi_part: float = 0.90, grounded: float = 0.90, verdict: str = "supported"):
        self.enabled = True
        self.multi_part = multi_part
        self.grounded = grounded
        self.verdict = verdict
        self.calls = 0
        self.states: list[dict] = []

    async def ask(self, state, questions):
        from iris_ai.jev.client import JevAnswers

        self.calls += 1
        self.states.append(state)
        return JevAnswers(
            nouls={"multi_part": self.multi_part, "grounded": self.grounded},
            choices={"verdict": self.verdict},
        )


def _orchestrator(tmp_path: Path, llm: LLMClient | None = None, jev=None, **budget):
    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, llm or ReportingLLM())
    runtime.jev = jev
    return Orchestrator(runtime, budget=TurnBudget(**budget)), runtime


# ── routing stays tool-initiated ─────────────────────────────────────────


async def test_constructing_an_orchestrator_does_nothing(tmp_path: Path):
    """No auto-research: making the object cannot call a model."""
    llm = ReportingLLM()
    orch, _ = _orchestrator(tmp_path, llm)
    assert orch is not None
    assert llm.calls == 0


async def test_no_delegation_means_no_subagent_calls(tmp_path: Path):
    llm = ReportingLLM()
    _orch, _ = _orchestrator(tmp_path, llm)
    assert llm.calls == 0  # a normal turn never touches this layer


# ── refusals ─────────────────────────────────────────────────────────────


async def test_the_master_switch_refuses_without_running_anything(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "multi_agent_enabled", False)
    llm = ReportingLLM()
    orch, _ = _orchestrator(tmp_path, llm)
    handoff = await orch.delegate("researcher", "when did we go to the beach?")
    assert handoff.refused == REFUSED_DISABLED
    assert handoff.claims == ()
    assert llm.calls == 0  # disabled means no model call at all


async def test_an_unknown_role_is_refused(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM())
    handoff = await orch.delegate("astrologer", "what is my sign?")
    assert handoff.refused == REFUSED_NO_ROLE


async def test_the_call_cap_refuses_and_says_why(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), max_calls=2)
    assert (await orch.delegate("researcher", "q1")).refused == ""
    assert (await orch.delegate("critic", "q2")).refused == ""
    third = await orch.delegate("researcher", "q3")
    assert third.refused == REFUSED_BUDGET
    assert third.claims == ()


async def test_a_blown_deadline_refuses(tmp_path: Path):
    """The clock is injected so the test does not sleep."""
    now = [0.0]
    orch, _ = _orchestrator(
        tmp_path, ReportingLLM(), deadline_ms=1000, clock=lambda: now[0]
    )
    assert (await orch.delegate("researcher", "q1")).refused == ""
    now[0] = 5.0  # five seconds of wall clock, deadline was 1s
    late = await orch.delegate("researcher", "q2")
    assert late.refused == REFUSED_DEADLINE


async def test_a_refusal_is_recorded_in_the_trace(tmp_path: Path):
    from iris_ai import turnlog

    orch, _ = _orchestrator(tmp_path, ReportingLLM(), max_calls=1)
    with turnlog.collect() as log:
        await orch.delegate("researcher", "q1")
        await orch.delegate("researcher", "q2")
    kinds = [e["kind"] for e in log.judgments]
    refusals = [e for e in log.judgments if e.get("refused")]
    assert "handoff" in kinds
    assert refusals and refusals[0]["refused"] == REFUSED_BUDGET


# ── the run itself ───────────────────────────────────────────────────────


async def test_delegate_returns_a_sourced_report(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    # Recall feedback is a write to the workspace; this test is about provenance.
    monkeypatch.setattr(settings, "recall_feedback_enabled", False)
    orch, runtime = _orchestrator(tmp_path, SearchThenReportLLM())

    class OneHitIndex:
        async def escalate(self, *args, **kwargs):
            from datetime import date

            from iris_ai.memory.index import MemoryHit
            from iris_ai.memory.provenance import Origin

            return [
                MemoryHit(
                    content="the beach trip was in August",
                    path="memory/2026-08-04.md",
                    score=1.0,
                    importance=5.0,
                    origin=Origin.OWNER,
                    observed_at=date(2026, 8, 4),
                    evergreen=False,
                    chunk_index=2,
                )
            ]

    runtime.index = OneHitIndex()  # type: ignore[assignment]
    handoff = await orch.delegate("researcher", "when did we go to the beach?")
    assert handoff.refused == ""
    assert handoff.claims and not handoff.claims[0].unsourced
    assert handoff.claims[0].sources[0].path == "memory/2026-08-04.md"


async def test_the_role_runs_on_its_declared_tier(tmp_path: Path):
    llm = ReportingLLM()
    orch, _ = _orchestrator(tmp_path, llm)
    await orch.delegate("researcher", "q")
    await orch.delegate("critic", "q")
    assert llm.tiers == ["cheap", "strong"]


async def test_an_unrunnable_role_cannot_break_the_turn(tmp_path: Path):
    """A role that throws is a report, not an exception — the same contract the
    pre-P5 worker had."""

    class ExplodingLLM(LLMClient):
        async def complete_with_tools(self, messages, tools=None, **kwargs):
            raise RuntimeError("provider down")

    orch, _ = _orchestrator(tmp_path, ExplodingLLM())
    handoff = await orch.delegate("researcher", "q")
    assert handoff.refused == ""
    assert handoff.claims == ()  # renders as "no findings"


# ── fan-out ──────────────────────────────────────────────────────────────


async def test_fan_out_preserves_the_order_asked_for(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev(multi_part=0.9), max_calls=5)
    handoffs = await orch.fan_out("researcher", ["first", "second", "third"])
    assert [h.question for h in handoffs] == ["first", "second", "third"]


async def test_fan_out_is_capped(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "multi_agent_max_parallel", 2)
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev(multi_part=0.9), max_calls=5)
    handoffs = await orch.fan_out("researcher", ["a", "b", "c", "d"])
    assert len(handoffs) == 2


async def test_fan_out_respects_the_remaining_call_budget(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev(multi_part=0.9), max_calls=1)
    handoffs = await orch.fan_out("researcher", ["a", "b"])
    assert len(handoffs) == 1
    assert handoffs[0].refused == ""  # the one it could afford


async def test_fan_out_collapses_to_one_without_the_effort_judgment(tmp_path: Path):
    """The documented failure mode is a swarm of subagents for a simple query.
    With no judgment (JEV unset), the width is one — the pre-P5 behaviour."""
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), max_calls=5)
    handoffs = await orch.fan_out("researcher", ["a", "b", "c"])
    assert len(handoffs) == 1


async def test_fan_out_is_blocked_below_the_effort_gate(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev(multi_part=0.05), max_calls=5)
    handoffs = await orch.fan_out("researcher", ["a", "b", "c"])
    assert len(handoffs) == 1


# ── verify: the sufficiency gate and one bounded revision ────────────────


async def test_a_grounded_draft_needs_no_critic_invocation(tmp_path: Path):
    """One cheap judgment instead of a whole subagent run."""
    llm = ReportingLLM()
    orch, _ = _orchestrator(tmp_path, llm, jev=FakeJev(grounded=0.95))
    handoff, revise = await orch.verify("the trip was in August")
    assert handoff.kind == "critique"
    assert handoff.verdict == "supported"
    assert not revise
    assert llm.calls == 0  # the critic never ran


async def test_an_ungrounded_draft_runs_the_critic_and_allows_one_revision(tmp_path: Path):
    llm = ReportingLLM("critic report")
    orch, _ = _orchestrator(tmp_path, llm, jev=FakeJev(grounded=0.05, verdict="unsupported"))
    handoff, revise = await orch.verify("you own a dog called Milo")
    assert llm.calls == 1
    assert handoff.verdict == "unsupported"
    assert handoff.score == 0.05
    assert handoff.gate == 0.60
    assert revise


async def test_only_one_revision_per_turn(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev(grounded=0.05), max_calls=9)
    assert (await orch.verify("d"))[1] is True
    assert (await orch.verify("d"))[1] is False  # the allowance is spent


async def test_revision_can_be_switched_off(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "multi_agent_revise_once", False)
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev(grounded=0.05))
    assert (await orch.verify("d"))[1] is False


async def test_verify_fails_open_without_jev(tmp_path: Path):
    """No judgment means the critic still does its job, but nothing is forced."""
    llm = ReportingLLM("critic report")
    orch, _ = _orchestrator(tmp_path, llm)
    handoff, revise = await orch.verify("d")
    assert llm.calls == 1
    assert handoff.score is None  # no judgment, so no score to report
    assert handoff.verdict == "partial"
    assert revise


async def test_verify_checks_the_draft_against_the_turns_findings(tmp_path: Path):
    """The evidence handed to the judgment must be what the specialists actually
    returned — a sufficiency judgment against nothing is a coin flip."""
    jev = FakeJev(grounded=0.05)
    orch, _ = _orchestrator(tmp_path, ReportingLLM("the trip was in August"), jev=jev)
    await orch.delegate("researcher", "when did we go to the beach?")
    handoff, _ = await orch.verify("the trip was in August")
    assert handoff.from_role == "critic"
    assert "the trip was in August" in jev.states[0]["findings"]


async def test_verify_is_refused_by_the_master_switch(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "multi_agent_enabled", False)
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), jev=FakeJev())
    handoff, revise = await orch.verify("d")
    assert handoff.refused == REFUSED_DISABLED
    assert not revise


# ── budget scope ─────────────────────────────────────────────────────────


async def test_the_allowance_is_per_turn_not_per_process(tmp_path: Path):
    """A cap of two means two *per turn*. The first draft the orchestrator
    carried one budget on the instance, so the cap would have been spent once,
    forever, on the first turn of the process."""
    orch, _ = _orchestrator(tmp_path, ReportingLLM(), max_calls=2)
    with orch.turn():
        assert (await orch.delegate("researcher", "q1")).refused == ""
        assert (await orch.delegate("researcher", "q2")).refused == ""
        assert (await orch.delegate("researcher", "q3")).refused == REFUSED_BUDGET
    with orch.turn():
        assert (await orch.delegate("researcher", "next turn")).refused == ""  # fresh again


async def test_the_turn_keeps_its_handoffs(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM("r"), max_calls=4)
    with orch.turn() as budget:
        await orch.delegate("researcher", "q1")
        await orch.delegate("researcher", "q2")
    assert len([h for h in budget.handoffs if h.kind == "report"]) == 2
    assert len([h for h in orch.budget.handoffs if h.kind == "report"]) == 0  # new turn


# ── merge ────────────────────────────────────────────────────────────────


def _report(text: str, sources: tuple[Source, ...] = ()) -> Handoff:
    return Handoff(
        id="h",
        from_role="researcher",
        to_role="lead",
        kind="report",
        claims=(Claim(text, sources=sources),),
        spend=Spend(ms=10),
    )


def test_merge_is_deterministic_in_input_order(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM())
    first = _report("the first finding", (Source("a.md"),))
    second = _report("the second finding", (Source("b.md"),))
    text, truncated = orch.merge([first, second])
    assert not truncated
    assert text.index("the first finding") < text.index("the second finding")
    # Same inputs, same output — merge is a pure function of the handoffs.
    assert orch.merge([first, second])[0] == text


def test_merge_marks_unsourced_claims(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM())
    text, _ = orch.merge([_report("the owner used to live in Lisbon")])
    assert "UNSOURCED" in text


def test_merge_skips_refusals(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM())
    refused = Handoff(
        id="h2", from_role="researcher", to_role="lead", kind="request", refused=REFUSED_BUDGET
    )
    text, _ = orch.merge([_report("the only finding", (Source("a.md"),)), refused])
    assert "the only finding" in text
    assert REFUSED_BUDGET not in text  # a refusal is not a finding


def test_merge_of_nothing_says_so(tmp_path: Path):
    orch, _ = _orchestrator(tmp_path, ReportingLLM())
    text, truncated = orch.merge([])
    assert not truncated
    assert "no findings" in text.lower()


def test_merge_truncates_at_the_derived_cap(tmp_path: Path):
    from iris_ai.config import settings

    orch, _ = _orchestrator(tmp_path, ReportingLLM())
    big = [_report("x" * 3000, (Source("a.md"),)) for _ in range(4)]
    text, truncated = orch.merge(big)
    cap = settings.multi_agent_max_output_chars * settings.multi_agent_max_calls
    assert truncated
    assert len(text) <= cap


# ── the budget object ────────────────────────────────────────────────────


def test_turn_budget_reports_what_is_left():
    b = TurnBudget(max_calls=2, deadline_ms=1000, clock=lambda: 0.0)
    assert b.exhausted() == ""
    assert b.remaining() == 2
    b.spend_call()
    assert b.remaining() == 1
    b.spend_call()
    assert b.exhausted() == REFUSED_BUDGET


def test_turn_budget_checks_the_clock():
    now = [0.0]
    b = TurnBudget(max_calls=5, deadline_ms=500, clock=lambda: now[0])
    assert b.exhausted() == ""
    now[0] = 0.6
    assert b.exhausted() == REFUSED_DEADLINE


@pytest.mark.parametrize("role", [RESEARCHER, CRITIC])
def test_every_declared_role_is_runnable(role):
    """A role the orchestrator cannot run is a declared capability that does not
    exist — the failure mode this test exists to prevent."""
    assert role.name in Orchestrator.RUNNABLE
