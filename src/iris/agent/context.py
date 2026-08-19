"""Context assembly — the *context engineering* layer.

Turns the memory engine into a prompt prefix, in a cache-friendly order:

1. Instructions (AGENTS.md) — static, never changes → cache-hit forever
2. User profile (USER.md) — static between dreams → cache-hit mostly
3. Curated memory (MEMORY.md) — changes only during sleep → cache-hit between sleeps
4. Relevant memories (trigger-injected search hits) — per-message → cache-miss only here

Per the research brief: stable-prefix ordering makes the top of the prompt
cache-friendly (cache reads ~10x cheaper than writes with two-tier pricing),
and the contextual-retrieval result (Anthropic: -67% retrieval failures) is
bought cheaply: a single deterministic hybrid search at session start.
"""

from __future__ import annotations

import re

from iris.agent.runtime import Runtime
from iris.config import settings
from iris.memory.provenance import Origin

# Deterministic, model-free detection of temporal/multi-hop questions —
# the escalation-lane trigger. Free tier friendly: no token cost.
_TEMPORAL_PATTERNS = re.compile(
    r"\b(when|whenever|what happened|what did|what was|how long ago|"
    r"yesterday|last (week|month|year|sunday|monday|tuesday|wednesday|thursday|friday|saturday)|"
    r"before|earlier|back then|used to|previously|long ago|"
    r"in (january|february|march|april|may|june|july|august|september|october|november|december|"
    r"20[0-9]{2}|the (morning|afternoon|evening))|"
    r"a few (days|weeks|months|years) (ago|later|before))\b",
    re.IGNORECASE,
)


def needs_escalation(message: str) -> bool:
    """True when a message asks about *when* something happened — the case
    the default lane's recency decay is designed to hide."""
    return bool(_TEMPORAL_PATTERNS.search(message))


class ContextAssembler:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def assemble(self, user_message: str, *, session_id: str) -> str:
        parts: list[str] = []

        instructions = self.runtime.files.read(self.runtime.files.instructions)
        if instructions:
            parts.append(f"## Operating contract\n{instructions}")

        profile = self.runtime.files.read(self.runtime.files.user)
        if profile.strip():
            parts.append(f"## Owner profile\n{profile}")

        curated = self.runtime.files.read(self.runtime.files.memory)
        if curated.strip():
            parts.append(f"## Long-term memory (curated)\n{curated}")

        # Trigger-injected recall: only ever from promoted (trusted) tiers
        if user_message.strip():
            hits = await self.runtime.index.search(
                user_message,
                top_k=settings.mrr_top_k,
                mrr_top_k=settings.trigger_inject_max,
                require_origin={Origin.OWNER, Origin.AGENT},
            )
            blocks: list[str] = []
            if hits:
                block = "\n".join(f"- ({h.origin.value}) {h.content}" for h in hits)
                blocks.append(f"## Relevant memories\n{block}")

            # Escalation lane: temporal/multi-hop questions, or a weak default
            # lane — old facts live in daily notes, not the precision-tuned lane.
            weak = not hits or hits[0].score < 0.15
            if needs_escalation(user_message) or weak:
                esc = await self.runtime.index.escalate(
                    user_message,
                    top_k=settings.mrr_top_k,
                    mrr_top_k=settings.trigger_inject_max,
                )
                seen = {h.path for h in hits}
                esc = [h for h in esc if h.path not in seen]
                if esc:
                    block = "\n".join(f"- ({h.observed_at}) {h.content}" for h in esc)
                    blocks.append(f"## Escalation lane (daily notes)\n{block}")

            parts.extend(blocks)

        # Skill trigger injection: only name + description + match, never the
        # full procedure — enough for the agent to decide whether to
        # skill_apply (which returns the procedure on demand).
        if user_message.strip():
            matched = self.runtime.skills.match_triggers(user_message)
            if matched:
                block = "\n".join(
                    f"- {s.name}: {s.description} (trigger match)" for s in matched
                )
                parts.append(f"## Relevant skills\n{block}")

        return "\n\n".join(parts)