"""Example: a context builder that adds a line the owner can see was injected."""

from __future__ import annotations


class RagFirstContext:
    """Takes `ctx`, not the runtime. Local and dotted components do not receive the kernel."""

    def __init__(self, ctx, **options) -> None:
        self.ctx = ctx
        self.options = options

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        del user_message, session_id
        return "Answer from the conversation.\n\n## Custom context\nThis prefix was built by RagFirstContext.", []

    async def assemble(self, request):
        """v1 entry. The rendered prefix matches `assemble_turn`."""
        from iris_ai.sdk.types import ContextBlock, ContextResult

        text, skills = await self.assemble_turn(request.message, session_id=request.session_id)
        return ContextResult(
            blocks=(ContextBlock(title="", text=text, kind="custom", priority=50),),
            skills=tuple(skills),
        )
