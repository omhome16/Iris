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

from iris.agent.runtime import Runtime
from iris.config import settings
from iris.memory.provenance import Origin


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
            if hits:
                block = "\n".join(f"- ({h.origin.value}) {h.content}" for h in hits)
                parts.append(f"## Relevant memories\n{block}")

        return "\n\n".join(parts)