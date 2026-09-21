"""Context assembly — the *context engineering* layer (memory orchestration v2).

Turns the memory engine into a prompt prefix, in a cache-friendly order:

1. Instructions (AGENTS.md) — static, never changes → cache-hit forever
2. User profile (USER.md) — static between dreams → cache-hit mostly
3. Curated memory (MEMORY.md) — changes only during sleep → cache-hit between sleeps
4. Relevant skills (trigger match) — lexical, zero model/embedding cost

Retrieval is agent-invoked now (memory_search / deep_dive tools); the
assembler runs no index search, no embeddings, and no research subagent
per turn. Trivial turns cost zero retrieval tokens.

Skill selection is the one JEV call on this path: choosing which of the stored
procedures is worth naming is a judgment, and the previous implementation was a
casefolded substring test. It stays behind a fallback so the assembled prefix
is identical when JEV is unavailable.
"""

from __future__ import annotations

from iris.agent.runtime import Runtime
from iris.jev import suggest_skill


class ContextAssembler:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def assemble(self, user_message: str, *, session_id: str) -> str:
        parts: list[str] = []

        # Bootstrap budgets are enforced here (bootstrap_user/bootstrap_memory
        # apply `user_profile_budget_tokens` / `bootstrap_budget_tokens`). The
        # budgets were configured but never applied: MEMORY.md entered the
        # prompt unbounded, so the one file that grows forever had no ceiling.
        instructions = self.runtime.files.read(self.runtime.files.instructions)
        if instructions:
            parts.append(f"## Operating contract\n{instructions}")

        profile = self.runtime.files.bootstrap_user()
        if profile.strip():
            parts.append(f"## Owner profile\n{profile}")

        curated = self.runtime.files.bootstrap_memory()
        if curated.strip():
            parts.append(f"## Long-term memory (curated)\n{curated}")

        skill_block = await self._skills_block(user_message)
        if skill_block:
            parts.append(skill_block)

        return "\n\n".join(parts)

    async def _skills_block(self, user_message: str) -> str:
        """Name only the skills worth looking at — never the procedure itself.

        Enough for the agent to decide whether to call `skill_apply` (which
        returns the full procedure on demand), and nothing more.
        """
        if not user_message.strip():
            return ""
        skills = self.runtime.skills.list()
        if not skills:
            return ""

        names: list[str] = []
        suggestion = await suggest_skill(
            getattr(self.runtime, "jev", None), message=user_message, skills=skills
        )
        if suggestion.screened:
            if suggestion.name:
                names = [suggestion.name]
        else:
            # Deterministic fallback: lexical trigger match, zero latency.
            names = [s.name for s in self.runtime.skills.match_triggers(user_message)]
        if not names:
            return ""

        by_name = {s.name: s for s in skills}
        lines = [
            f"- {name}: {by_name[name].description} (matched this turn)"
            for name in names
            if name in by_name
        ]
        if not lines:
            return ""
        return "## Relevant skills\n" + "\n".join(lines)
