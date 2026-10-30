"""Recall-first context. The budget is spent on memory.

Every recalled hit is a memory block at priority 0, so a trim drops other
notes before it drops a recall. Stale and current lines are both shown as
current. That is the point: this stage buys recall, not supersession.
"""

from __future__ import annotations


def _content(hit: object) -> str:
    if isinstance(hit, dict):
        return str(hit.get("content") or "")
    return str(getattr(hit, "content", "") or "")


class RecallFirst:
    permissions = ("memory.read",)

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options

    async def assemble(self, request):
        from iris_ai.sdk.types import ContextBlock, ContextResult

        hits = []
        memory = getattr(self.ctx, "memory", None) if self.ctx is not None else None
        if memory is not None:
            hits = await memory.search(getattr(request, "message", ""), top_k=8)
        blocks = [
            ContextBlock(title="Recalled", text=_content(hit), kind="memory", priority=0)
            for hit in hits
            if _content(hit)
        ]
        blocks.append(ContextBlock(title="Scratch", text="unrecalled note " * 8, kind="note", priority=90))
        return ContextResult(blocks=tuple(blocks))
