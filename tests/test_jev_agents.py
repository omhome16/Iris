"""P5 — the two multi-agent judgments, and how they fail.

Every JEV integration in Iris is best-effort: it must degrade to the
deterministic path, never raise, and never fail closed. These tests pin that for
both judgments, plus the one thing the sufficiency judgment exists for — a draft
that is not grounded comes back marked, so the answer cannot be asserted as fact.
"""

from __future__ import annotations

import pytest

from iris.jev.agents import judge_effort, judge_sufficiency
from iris.jev.client import JevAnswers


class FakeJev:
    """A JEV client that returns canned answers, counting requests."""

    def __init__(self, answers: JevAnswers | None) -> None:
        self.enabled = True
        self._answers = answers
        self.calls = 0
        self.states: list[dict] = []
        self.questions: list[dict] = []

    async def ask(self, state, questions):
        self.calls += 1
        self.states.append(state)
        self.questions.append(questions)
        return self._answers


class DisabledJev(FakeJev):
    def __init__(self) -> None:
        super().__init__(None)
        self.enabled = False


def _answers(**kw) -> JevAnswers:
    return JevAnswers(nouls=kw.get("nouls", {}), choices=kw.get("choices", {}))


# ── effort ───────────────────────────────────────────────────────────────


async def test_effort_above_the_gate_allows_fan_out():
    jev = FakeJev(_answers(nouls={"multi_part": 0.91}))
    verdict = await judge_effort(jev, question="who did we meet in Lisbon and Berlin?")
    assert verdict.screened
    assert verdict.fan_out


async def test_effort_below_the_gate_blocks_fan_out():
    jev = FakeJev(_answers(nouls={"multi_part": 0.12}))
    verdict = await judge_effort(jev, question="what is my cat called?")
    assert verdict.screened
    assert not verdict.fan_out


async def test_effort_is_calibrated_by_config(monkeypatch):
    """The gate is code, not the model: the same score flips on the threshold."""
    from iris.config import settings

    jev = FakeJev(_answers(nouls={"multi_part": 0.62}))
    assert (await judge_effort(jev, question="q")).fan_out  # default gate 0.60
    monkeypatch.setattr(settings, "multi_agent_effort_gate", 0.90)
    assert not (await judge_effort(jev, question="q")).fan_out


@pytest.mark.parametrize("jev", [None, DisabledJev()])
async def test_effort_fails_open_without_jev(jev):
    verdict = await judge_effort(jev, question="who did we meet in Lisbon and Berlin?")
    assert not verdict.screened
    assert not verdict.fan_out  # deterministic path: no fan-out
    assert verdict.reason


async def test_effort_never_asks_a_request_without_a_question():
    jev = FakeJev(_answers(nouls={"multi_part": 0.99}))
    verdict = await judge_effort(jev, question="   ")
    assert not verdict.screened
    assert jev.calls == 0


async def test_effort_survives_a_failed_request():
    jev = FakeJev(None)  # ask() returns None, the way a real failure does
    verdict = await judge_effort(jev, question="anything")
    assert not verdict.screened
    assert not verdict.fan_out


async def test_effort_is_recorded_in_the_trace():
    from iris import turnlog

    jev = FakeJev(_answers(nouls={"multi_part": 0.88}))
    with turnlog.collect() as log:
        await judge_effort(jev, question="q")
    entry = next(e for e in log.judgments if e["kind"] == "agent_effort")
    assert entry["screened"] is True
    assert entry["fan_out"] is True
    assert entry["multi_part"] == 0.88


# ── sufficiency ──────────────────────────────────────────────────────────


async def test_a_grounded_draft_is_not_sent_back():
    jev = FakeJev(_answers(nouls={"grounded": 0.93}, choices={"verdict": "supported"}))
    verdict = await judge_sufficiency(jev, draft="The trip was in August.", findings="- Aug 4")
    assert verdict.screened
    assert verdict.verdict == "supported"
    assert not verdict.below_gate


async def test_an_ungrounded_draft_is_marked_for_one_revision():
    jev = FakeJev(_answers(nouls={"grounded": 0.04}, choices={"verdict": "unsupported"}))
    verdict = await judge_sufficiency(jev, draft="You own a dog called Milo.", findings="(none)")
    assert verdict.screened
    assert verdict.verdict == "unsupported"
    assert verdict.below_gate


async def test_sufficiency_ignores_an_unexpected_verdict_label():
    """A label outside the defined set must not become a claim about grounding."""
    jev = FakeJev(_answers(nouls={"grounded": 0.10}, choices={"verdict": "probably fine"}))
    verdict = await judge_sufficiency(jev, draft="d", findings="f")
    assert verdict.verdict == "partial"
    assert verdict.below_gate


@pytest.mark.parametrize("jev", [None, DisabledJev()])
async def test_sufficiency_fails_open_without_jev(jev):
    """Fail open means *no forced revision* — the deterministic path is the
    pre-P5 one, not a stricter one."""
    verdict = await judge_sufficiency(jev, draft="d", findings="f")
    assert not verdict.screened
    assert not verdict.below_gate


async def test_sufficiency_never_spends_a_request_on_an_empty_draft():
    jev = FakeJev(_answers(nouls={"grounded": 0.0}, choices={"verdict": "unsupported"}))
    verdict = await judge_sufficiency(jev, draft="   ", findings="f")
    assert not verdict.screened
    assert jev.calls == 0
    assert not verdict.below_gate


async def test_sufficiency_hands_the_findings_to_the_judgment():
    """The judgment is only meaningful if it sees the evidence it judges against."""
    jev = FakeJev(_answers(nouls={"grounded": 0.5}, choices={"verdict": "partial"}))
    await judge_sufficiency(jev, draft="the beach trip was in August", findings="memory/2026-08-04.md")
    state = jev.states[0]
    assert "memory/2026-08-04.md" in state["findings"]
    assert "the beach trip was in August" in state["draft"]


async def test_sufficiency_notes_that_there_are_no_findings():
    jev = FakeJev(_answers(nouls={"grounded": 0.1}, choices={"verdict": "unsupported"}))
    await judge_sufficiency(jev, draft="d", findings="")
    assert "no findings" in jev.states[0]["findings"].lower()


async def test_sufficiency_survives_a_failed_request():
    jev = FakeJev(None)
    verdict = await judge_sufficiency(jev, draft="d", findings="f")
    assert not verdict.screened
    assert not verdict.below_gate
    assert verdict.reason


async def test_sufficiency_is_recorded_in_the_trace():
    from iris import turnlog

    jev = FakeJev(_answers(nouls={"grounded": 0.22}, choices={"verdict": "partial"}))
    with turnlog.collect() as log:
        await judge_sufficiency(jev, draft="d", findings="f")
    entry = next(e for e in log.judgments if e["kind"] == "answer_check")
    assert entry["screened"] is True
    assert entry["below_gate"] is True
    assert entry["verdict"] == "partial"
