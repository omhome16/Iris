"""Example: a context builder that adds a line the owner can see was injected."""

from __future__ import annotations


class RagFirstContext:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        from iris_ai.agent.context import ContextAssembler

        text, skills = await ContextAssembler(self.runtime).assemble_turn(
            user_message, session_id=session_id
        )
        return text + "\n\n## Custom context\nThis prefix was built by RagFirstContext.", skills

    async def assemble(self, request):
        """v1 entry. The rendered prefix matches `assemble_turn`."""
        from iris_ai.sdk.types import ContextBlock, ContextResult

        text, skills = await self.assemble_turn(request.message, session_id=request.session_id)
        return ContextResult(
            blocks=(ContextBlock(title="", text=text, kind="custom", priority=50),),
            skills=tuple(skills),
        )
