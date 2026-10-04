"""Newest-first recall. Older facts in the same cluster go to history.

`extends = default` in the manifest. This stage only adds the recall and
history blocks. It loses on tokens: both the new fact and the old one are shown.
"""

from __future__ import annotations

import re


def _tokens(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z]{3,}", text)}


def layout(corpus: list[dict]) -> list[dict]:
    """Cluster by token overlap. Newest line is current. Older overlaps are history."""
    ordered = sorted(corpus, key=lambda row: int(row.get("days_ago") or 0))
    if not ordered:
        return []
    newest = ordered[0]
    newest_tokens = _tokens(str(newest.get("content") or ""))
    history = []
    for row in ordered[1:]:
        if len(_tokens(str(row.get("content") or "")) & newest_tokens) >= 2:
            history.append(str(row.get("content") or ""))
    blocks = [
        {
            "title": "Recalled",
            "kind": "recall",
            "text": str(newest.get("content") or ""),
            "source": str(newest.get("path") or ""),
        }
    ]
    if history:
        blocks.append({"title": "History", "kind": "history", "text": "\n".join(history)})
    return blocks


class TemporalRag:
    """v1 context stage. `assemble` returns a ContextResult."""

    api_version = "iris/v1"
    permissions = ("memory.read",)

    def __init__(self, ctx, **options) -> None:
        self.ctx = ctx
        self.options = options

    async def assemble(self, request):
        from iris_ai.sdk.types import ContextBlock, ContextResult

        prior = getattr(request, "prior", None)
        blocks = list(getattr(prior, "blocks", ()) or ())
        # Without an index, the stage still returns the prior prefix.
        extra = []
        if self.ctx is not None and getattr(self.ctx, "memory", None) is not None:
            hits = await self.ctx.memory.search(getattr(request, "message", ""), top_k=8)
            corpus = [{"content": hit.content, "path": hit.path, "days_ago": 0} for hit in hits]
            extra = layout(corpus)
        rendered = [
            ContextBlock(
                title=str(block.get("title") or ""),
                text=str(block.get("text") or ""),
                kind=str(block.get("kind") or "recall"),
                source=str(block.get("source") or ""),
                priority=30 if block.get("kind") == "history" else 15,
            )
            for block in extra
        ]
        return ContextResult(blocks=(*blocks, *rendered))
