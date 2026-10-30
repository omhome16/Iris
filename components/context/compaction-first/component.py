"""Compaction-first context. Older recalls become one history line.

The newest hit stays verbatim, as its own memory block. Everything before it
is folded into a single history block. A trim can drop the summary. It cannot
drop the tail without dropping the summary first, because the tail's priority
is lower.
"""

from __future__ import annotations


def _content(hit: object) -> str:
    if isinstance(hit, dict):
        return str(hit.get("content") or "")
    return str(getattr(hit, "content", "") or "")


class CompactionFirst:
    permissions = ("memory.read",)

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options

    async def assemble(self, request):
        from iris_ai.sdk.types import ContextBlock, ContextResult

        hits = []
        memory = getattr(self.ctx, "memory", None) if self.ctx is not None else None
        if memory is not None:
            hits = list(await memory.search(getattr(request, "message", ""), top_k=8) or [])
        texts = [_content(hit) for hit in hits if _content(hit)]
        blocks = []
        if len(texts) > 1:
            summary = "Earlier: " + " ".join(texts[:-1])
            blocks.append(ContextBlock(title="Earlier", text=summary, kind="history", priority=40))
        if texts:
            blocks.append(ContextBlock(title="Recent", text=texts[-1], kind="memory", priority=0))
        return ContextResult(blocks=tuple(blocks))
