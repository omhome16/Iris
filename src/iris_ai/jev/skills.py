"""Skill suggestion with JEV.

`SkillLibrary.match_triggers` is a casefolded substring test. It has two
failure modes that matter for a procedural-memory system: a skill only ever
surfaces if the owner's wording happens to contain one of its trigger phrases,
and a short trigger can fire on an unrelated word ("svg" matching inside
"svg-pro"). An agent that never loads the right procedure never reinforces it,
which is how the skills tier silently starves.

Pattern: https://docs.typesafe.ai/cookbooks/skill_suggestion — rank the whole
roster against the turn, ask whether a skill is needed *at all*, and let a
confidence gate decide. Iris's roster is small enough that one request covers
every skill, so the cookbook's second verification pass stays unnecessary
until the roster grows.

The substring matcher is kept as the deterministic fallback: with JEV absent,
behaviour is identical to before.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from iris_ai import turnlog
from iris_ai.config import settings
from iris_ai.jev.client import JevClient, choice, noul

log = logging.getLogger("iris_ai.jev.skills")

_NONE = "none_of_these"


@dataclass(frozen=True, slots=True)
class SkillSuggestion:
    name: str | None = None
    needs_skill: float = 0.0
    confidence: float = 0.0
    screened: bool = False
    reason: str = ""


async def suggest_skill(
    jev: JevClient | None,
    *,
    message: str,
    skills: Sequence,
    max_candidates: int | None = None,
) -> SkillSuggestion:
    """At most one skill for this turn, or none. `screened=False` means JEV
    did not run and the caller should use the deterministic matcher."""
    if jev is None or not jev.enabled or not (message or "").strip() or not skills:
        turnlog.record("skill", screened=False, reason="suggestion disabled or unavailable")
        return SkillSuggestion(reason="suggestion disabled or unavailable")
    cap = max_candidates if max_candidates is not None else settings.jev_skill_candidates
    roster = list(skills)[: max(1, cap)]
    criteria: dict[str, object] = {
        s.name: (f"{s.description} · triggers: {', '.join(s.triggers)}" if s.triggers else s.description)
        for s in roster
    }
    criteria[_NONE] = "No stored skill applies to this turn."
    state = {
        "request": message[:4000],
        "skills": [
            {"name": s.name, "description": s.description, "triggers": list(s.triggers)} for s in roster
        ],
    }
    questions = {
        "fits": choice(
            "Which single stored skill should handle `request`? Choose `none_of_these` unless a "
            "skill's described procedure genuinely applies to what is being asked.",
            criteria,
        ),
        "needs_skill": noul(
            "Does `request` call for following a stored procedure, or is it an ordinary "
            "conversation the assistant can just answer?",
            true="It asks for a task or workflow that a stored procedure would cover.",
            false="It is ordinary conversation, a question, or something no procedure fits.",
        ),
    }
    answers = await jev.ask(state, questions)
    if answers is None:
        turnlog.record("skill", screened=False, reason="jev request failed")
        return SkillSuggestion(reason="jev request failed")

    picked = answers.choice("fits")
    confidence = answers.confidence("fits")
    needs = answers.noul("needs_skill")
    turnlog.record(
        "skill",
        screened=True,
        picked=picked,
        needs_skill=needs,
        confidence=confidence,
        roster=len(roster),
    )
    if not picked or picked == _NONE:
        log.debug("skill suggestion: no skill fits (needs=%.2f)", needs)
        return SkillSuggestion(needs_skill=needs, confidence=confidence, screened=True, reason="no skill fits")
    if needs < settings.jev_skill_gate:
        return SkillSuggestion(
            needs_skill=needs, confidence=confidence, screened=True, reason=f"gate {needs:.2f} below threshold"
        )
    if confidence < settings.jev_skill_min_confidence:
        return SkillSuggestion(
            needs_skill=needs,
            confidence=confidence,
            screened=True,
            reason=f"confidence {confidence:.2f} below threshold",
        )
    # Only ever suggest a skill that actually exists in the roster: a model
    # cannot pick an omitted option, but defending the boundary is cheap.
    if picked not in {s.name for s in roster}:
        return SkillSuggestion(needs_skill=needs, confidence=confidence, screened=True, reason="unknown skill name")
    return SkillSuggestion(name=picked, needs_skill=needs, confidence=confidence, screened=True)
