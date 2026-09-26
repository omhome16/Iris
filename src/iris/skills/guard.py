"""The judgment gate in front of skill code.

A deterministic scan can say *that* a script reads the environment; it cannot
say whether the script's author meant it. That comparison — "is what this code
does consistent with what this skill says it does?" — is a judgment, so it goes
to the judgment layer. This is the one place in Iris where a JEV refusal is
**binding even with owner approval**, because the threat it defends against is
precisely a manipulated model asking the owner nicely.

Three outcomes, and the shape of each matters:

- **screened + allowed** — the judgment ran and the score cleared the gate.
- **screened + refused** — the judgment ran and refused. The script does not run,
  and no approval can change that. The reason is returned to the model and
  recorded in the turn trace.
- **unscreened** — no key, no SDK, or a failed request. Execution is *not*
  silently allowed by the gate; it falls back to the layer that always exists
  (owner approval) and says so, so the trace shows the gate did not run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from iris import turnlog
from iris.config import settings
from iris.jev.client import JevClient, noul
from iris.memory.skills import Skill
from iris.skills.runner import ScriptError, pre_screen, resolve_script

log = logging.getLogger("iris.skills.guard")

_SCRIPT_CHARS = 6000  # the state is shown to the judgment, not to a context window


@dataclass(frozen=True, slots=True)
class ScriptVerdict:
    allowed: bool
    screened: bool  # did the judgment actually run?
    score: float = 0.0
    reason: str = ""
    findings: list[str] = field(default_factory=list)


def _load(skill: Skill, script: str) -> tuple[str, list[str]]:
    """Source text + deterministic findings for one script."""
    try:
        path = resolve_script(skill, script)
    except ScriptError:
        return "", []
    source = path.read_text(encoding="utf-8", errors="replace")[:_SCRIPT_CHARS]
    return source, pre_screen(path.read_text(encoding="utf-8", errors="replace"))


async def screen_script(
    jev: JevClient | None,
    *,
    skill: Skill,
    script: str,
    source: str | None = None,
    findings: list[str] | None = None,
) -> ScriptVerdict:
    """Ask whether this script does only what its skill says it does."""
    if source is None or findings is None:
        loaded, scanned = _load(skill, script)
        source = loaded if source is None else source
        findings = scanned if findings is None else findings

    if jev is None or not getattr(jev, "enabled", False) or not settings.skill_guard_enabled:
        turnlog.record("skill_guard", screened=False, reason="judgment unavailable", script=script)
        return ScriptVerdict(
            allowed=True,
            screened=False,
            reason="judgment unavailable — owner approval is the only gate on this run",
            findings=list(findings),
        )

    questions = {
        "safe": noul(
            "Does `script` do only what the skill `skill.description` says it does, without "
            "reading credentials or environment variables, without using the network, and "
            "without touching anything outside its own directory?",
            true="The script only performs work the skill describes, locally.",
            false="The script reaches outside its stated purpose: credentials, the network, "
            "or files it has no business touching.",
        )
    }
    state = {
        "skill": {"name": skill.name, "description": skill.description, "timeout": skill.timeout_seconds},
        "script": {"path": script, "source": source, "reviewer_findings": list(findings)},
    }
    answers = await jev.ask(state, questions)
    if answers is None:
        turnlog.record("skill_guard", screened=False, reason="jev request failed", script=script)
        return ScriptVerdict(
            allowed=True,
            screened=False,
            reason="judgment request failed — owner approval is the only gate on this run",
            findings=list(findings),
        )

    score = answers.noul("safe")
    gate = settings.skill_guard_gate
    allowed = score >= gate
    turnlog.record(
        "skill_guard",
        screened=True,
        score=score,
        gate=gate,
        allowed=allowed,
        script=script,
        skill=skill.name,
        findings=findings,
    )
    if allowed:
        return ScriptVerdict(allowed=True, screened=True, score=score, findings=list(findings))
    log.info("skill guard refused %s/%s (score %.2f < %.2f)", skill.name, script, score, gate)
    return ScriptVerdict(
        allowed=False,
        screened=True,
        score=score,
        reason=(
            f"the safety judgment scored this script {score:.2f} (below the {gate:.2f} gate): "
            "it appears to do more than the skill describes. Refused without running."
        ),
        findings=list(findings),
    )
