"""Context assembly — the *context engineering* layer (memory orchestration v2).

Turns the memory engine into a prompt prefix, in a cache-friendly order:

1. Instructions (AGENTS.md) — static, never changes → cache-hit forever
2. User profile (USER.md) — static between dreams → cache-hit mostly
3. Curated memory (MEMORY.md) — changes only during sleep → cache-hit between sleeps
4. Relevant skills (trigger match) — lexical, zero model/embedding cost

Retrieval is agent-invoked now (memory_search / deep_dive tools); the
assembler runs no index search, no embeddings, and no research subagent
per turn. Trivial turns cost zero retrieval tokens.
"""

from __future__ import annotations

from iris.agent.runtime import Runtime


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

        # Skill trigger injection: only name + description + match, never the
        # full procedure — enough for the agent to decide whether to
        # skill_apply (which returns the procedure on demand). Lexical only.
        if user_message.strip():
            matched = self.runtime.skills.match_triggers(user_message)
            if matched:
                block = "\n".join(
                    f"- {s.name}: {s.description} (trigger match)" for s in matched
                )
                parts.append(f"## Relevant skills\n{block}")

        return "\n\n".join(parts)