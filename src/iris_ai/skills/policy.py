"""Skill policy — what an active skill allows, enforced at the one choke point.

The manifest's `allowed-tools` field is a *narrowing*, and this module is where
that promise is kept. Two rules, both deliberate:

1. **Empty means unrestricted.** Every skill that existed before P4 declared
   nothing, and a skill that restricts nothing must not change a turn.
2. **A skill can only intersect.** It never adds a tool, so it cannot resurrect
   something the session was already denied (a scheduled task's session rule is
   applied *before* the policy, and separately from it).

Enforcement happens in `dispatch`, the single place every tool call passes
through, and again when the schemas are offered to the model — a model that is
never shown a tool is less likely to try it, but the refusal has to hold either
way. A denial is recorded in the turn trace, because a refusal nobody can see is
indistinguishable from a tool that broke.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from iris_ai.memory.skills import Skill


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    reason: str = ""
    skill: str = ""


class SkillPolicy:
    """The union of the active skills' allowlists, applied to one turn."""

    def __init__(self, active: Sequence[Skill] = ()) -> None:
        self.active = list(active)

    @property
    def restricted(self) -> bool:
        return bool(self.allowed_tools)

    @property
    def allowed_tools(self) -> set[str]:
        """Union of the non-empty allowlists. Empty set = no skill restricts."""
        allowed: set[str] = set()
        for skill in self.active:
            allowed.update(skill.allowed_tools or ())
        return allowed

    def check(self, tool: str) -> PolicyDecision:
        if not self.restricted:
            return PolicyDecision(True)
        if tool in self.allowed_tools:
            return PolicyDecision(True, skill=self._owner_of(tool))
        restricting = [s.name for s in self.active if s.allowed_tools]
        return PolicyDecision(
            False,
            reason=(
                f"the active skill {', '.join(repr(n) for n in restricting)} restricts this turn "
                f"to: {', '.join(sorted(self.allowed_tools))} — {tool!r} is not in that list"
            ),
            skill=restricting[0] if restricting else "",
        )

    def filter_schemas(self, schemas: Sequence[dict]) -> list[dict]:
        """The same rule, applied to what the model is offered."""
        if not self.restricted:
            return list(schemas)
        allowed = self.allowed_tools
        return [
            s
            for s in schemas
            if (s.get("function") or {}).get("name") in allowed
        ]

    def _owner_of(self, tool: str) -> str:
        for skill in self.active:
            if tool in (skill.allowed_tools or ()):
                return skill.name
        return ""


def policy_for(skills: object, names: Sequence[str] | None) -> SkillPolicy:
    """Resolve active skill names against a registry (unknown names ignored).

    An unknown name is not an error: the name comes from session state, and a
    skill deleted between two turns must not lock a turn out of its own tools.
    """
    resolved: list[Skill] = []
    for name in names or ():
        getter = getattr(skills, "get", None)
        skill = getter(name) if callable(getter) else None
        if skill is not None:
            resolved.append(skill)
    return SkillPolicy(resolved)
