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

import hashlib

from iris_ai import turnlog
from iris_ai.agent.runtime import Runtime
from iris_ai.jev import suggest_skill


class ContextAssembler:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime

    async def assemble(self, user_message: str | object, *, session_id: str = "") -> str | object:
        """Prefix string for a message, or a ContextResult when given a request."""
        if not isinstance(user_message, str):
            return await self.assemble_result(
                getattr(user_message, "message", ""),
                session_id=getattr(user_message, "session_id", session_id),
            )
        text, _skills = await self.assemble_turn(user_message, session_id=session_id)
        return text

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        """The prefix **and** which skills it just named.

        The graph needs the second half: naming a skill in the prompt is a
        promise that the turn will obey that skill's policy, and only the
        assembler knows which skill it actually chose.
        """
        result = await self.assemble_result(user_message, session_id=session_id)
        prefix = result.render()
        active = list(result.skills)
        # Record which prompt policy built this turn, plus a fingerprint of the
        # prefix it actually produced. Without both, "quality changed" cannot be
        # attributed to a prompt edit rather than a model or a corpus change —
        # the vault's "can't tell which prompt caused it" failure. The version
        # is deliberate and bumped by hand; the fingerprint catches the case
        # where someone edited a prompt and forgot to bump it.
        turnlog.note_prompt(hashlib.sha256(prefix.encode("utf-8")).hexdigest()[:12])
        return prefix, active

    async def assemble_result(self, user_message: str, *, session_id: str):
        """The same prefix as `assemble_turn`, as blocks."""
        from iris_ai.sdk.types import ContextBlock, ContextResult

        blocks: list[ContextBlock] = []
        instructions = self.runtime.files.read(self.runtime.files.instructions)
        if instructions:
            blocks.append(
                ContextBlock(
                    title="Operating contract",
                    text=instructions,
                    source="AGENTS.md",
                    kind="contract",
                    priority=0,
                )
            )
        profile = self.runtime.files.bootstrap_user()
        if profile.strip():
            blocks.append(ContextBlock(title="Owner profile", text=profile, source="USER.md", kind="profile", priority=10))
        curated = self.runtime.files.bootstrap_memory()
        if curated.strip():
            blocks.append(
                ContextBlock(
                    title="Long-term memory (curated)",
                    text=curated,
                    source="MEMORY.md",
                    kind="memory",
                    priority=20,
                )
            )
        skill_text, active = await self._skills_block(user_message)
        if skill_text:
            blocks.append(ContextBlock(title="Relevant skills", text=skill_text, kind="skills", priority=40))
        return ContextResult(blocks=tuple(blocks), skills=tuple(active))

    async def _skills_block(self, user_message: str) -> tuple[str, list[str]]:
        """Name only the skills worth looking at — never the procedure itself.

        Enough for the agent to decide whether to call `skill_apply` (which
        returns the full procedure on demand), and nothing more.
        """
        if not user_message.strip():
            return "", []
        # selectable(), not list(): a disabled or invalid skill must never be
        # named in the prompt, because naming it activates its policy.
        skills = self.runtime.skills.selectable()
        if not skills:
            return "", []

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
            return "", []

        by_name = {s.name: s for s in skills}
        named = [name for name in names if name in by_name]
        lines = [
            f"- {name}: {by_name[name].description} (matched this turn)" for name in named
        ]
        if not lines:
            return "", []
        return "\n".join(lines), named
