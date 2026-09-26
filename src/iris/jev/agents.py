"""Multi-agent judgments with JEV.

Two decisions on the multi-agent path are *judgments*, not arithmetic, and both
are places where a hand-tuned heuristic goes wrong in a documented way:

**Effort** — is this actually several independent things, or one question? The
documented failure of production multi-agent systems is the opposite of
under-delegation: spawning a swarm of subagents for a simple query, burning ~15x
the tokens of a chat for nothing. Below `multi_agent_effort_gate` the lead gets
no fan-out.

**Sufficiency** — is every factual claim in this draft actually supported by the
findings? This is the one judgment the product promise depends on ("memory you
can see and trust", *never fabricate*). It is also the judgment the
self-correction literature says must not be left to the generator: an LLM
reviewing its own work mostly agrees with itself, so the verdict comes from a
different model tier than the prose and gates a bounded revision.

Contract, as everywhere else in `iris.jev`: code owns the thresholds and the
action; JEV supplies only the probability. Both fail **open** — with JEV unset,
unreachable or slow, the deterministic path runs (no fan-out, no forced
revision), which is exactly Iris's behaviour before this module existed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from iris import turnlog
from iris.config import settings
from iris.jev.client import JevClient, choice, noul

log = logging.getLogger("iris.jev.agents")

VERDICTS: tuple[str, ...] = ("supported", "unsupported", "partial")

# Per-field caps: these are prompts about one question, not a whole transcript.
_MAX_QUESTION = 4000
_MAX_DRAFT = 4000
_MAX_FINDINGS = 6000


@dataclass(frozen=True, slots=True)
class EffortJudgment:
    multi_part: float = 0.0
    fan_out: bool = False
    screened: bool = False
    reason: str = ""


@dataclass(frozen=True, slots=True)
class SufficiencyJudgment:
    grounded: float = 0.0
    verdict: str = ""
    below_gate: bool = False
    screened: bool = False
    reason: str = ""


async def judge_effort(jev: JevClient | None, *, question: str) -> EffortJudgment:
    """Is this question several independent searches, or one?

    `screened=False` means JEV did not answer: the caller must use the
    deterministic path (one bounded call, no fan-out).
    """
    if jev is None or not jev.enabled or not (question or "").strip():
        turnlog.record("agent_effort", screened=False, reason="judgment disabled or unavailable")
        return EffortJudgment(reason="judgment disabled or unavailable")

    answers = await jev.ask(
        {"request": question[:_MAX_QUESTION]},
        {
            "multi_part": noul(
                "Does `request` ask for several independent things that would each need their "
                "own search to answer, or is it one question?",
                true=(
                    "It asks for several independent things, each of which would need its own "
                    "search to answer."
                ),
                false=(
                    "It is one question — however long or detailed — or a single lookup that "
                    "one search would answer."
                ),
            )
        },
    )
    if answers is None:
        turnlog.record("agent_effort", screened=False, reason="jev request failed")
        return EffortJudgment(reason="jev request failed")

    multi_part = answers.noul("multi_part")
    fan_out = multi_part >= settings.multi_agent_effort_gate
    turnlog.record("agent_effort", screened=True, multi_part=multi_part, fan_out=fan_out)
    return EffortJudgment(multi_part=multi_part, fan_out=fan_out, screened=True)


async def judge_sufficiency(
    jev: JevClient | None, *, draft: str, findings: str
) -> SufficiencyJudgment:
    """Is every factual claim in `draft` supported by `findings`?

    `below_gate=True` means the draft is not grounded enough to assert as-is:
    the caller allows exactly one revision, and after that the lead must say what
    it could not ground instead of asserting it.

    An empty draft is nothing to verify and costs no request.
    """
    if not (draft or "").strip():
        return SufficiencyJudgment(reason="nothing to verify")
    if jev is None or not jev.enabled:
        turnlog.record("answer_check", screened=False, reason="judgment disabled or unavailable")
        return SufficiencyJudgment(reason="judgment disabled or unavailable")

    answers = await jev.ask(
        {
            "draft": draft[:_MAX_DRAFT],
            "findings": (findings or "")[:_MAX_FINDINGS] or "(no findings were returned)",
        },
        {
            "grounded": noul(
                "Look at every factual claim in `draft` and check it against `findings`. Is "
                "every factual claim in `draft` supported by `findings`?",
                true=(
                    "Every factual claim in the draft can be traced to something in the "
                    "findings."
                ),
                false=(
                    "At least one factual claim in the draft is not supported by the findings — "
                    "it is invented, contradicted, or asserted with no source at all."
                ),
            ),
            "verdict": choice(
                "How well is `draft` grounded in `findings`?",
                {
                    "supported": "Every factual claim is traced to the findings.",
                    "partial": "Most claims are traced, but at least one is not.",
                    "unsupported": (
                        "The draft asserts facts the findings do not carry, or contradicts them."
                    ),
                },
            ),
        },
    )
    if answers is None:
        turnlog.record("answer_check", screened=False, reason="jev request failed")
        return SufficiencyJudgment(reason="jev request failed")

    grounded = answers.noul("grounded")
    verdict = answers.choice("verdict", "partial")
    if verdict not in VERDICTS:
        verdict = "partial"  # an unexpected label must not become a claim about good grounding
    below_gate = grounded < settings.multi_agent_critique_gate
    turnlog.record(
        "answer_check",
        screened=True,
        grounded=grounded,
        verdict=verdict,
        gate=settings.multi_agent_critique_gate,
        below_gate=below_gate,
        confidence=answers.confidence("verdict"),
    )
    return SufficiencyJudgment(
        grounded=grounded, verdict=verdict, below_gate=below_gate, screened=True
    )
