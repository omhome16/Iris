"""Merge by token overlap. No supersession and no conflict record.

When the incoming line shares a subject word with a curated line, the plan
adds one sentence that contains both. The old line is left in place by this
stage. A caller that wanted retirement uses the supersede consolidator.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z]{4,}")


def _tokens(text: str) -> set[str]:
    return {word.lower() for word in _TOKEN.findall(text)}


class MergeSimilarity:
    permissions = ("memory.read",)

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options

    async def propose(self, request):
        from iris_ai.sdk.types import ConsolidationPlan, MemoryCandidate

        incoming = str(getattr(request, "notes", "") or "").strip()
        curated = str(getattr(request, "curated", "") or "")
        if not incoming:
            return ConsolidationPlan()
        incoming_tokens = _tokens(incoming)
        partner = ""
        for line in curated.splitlines():
            if line.strip() and len(_tokens(line) & incoming_tokens) >= 1:
                partner = line.strip().lstrip("- ").strip()
                break
        if partner:
            content = f"{partner} Also: {incoming}"
        else:
            content = incoming
        return ConsolidationPlan(add=(MemoryCandidate(content=content, kind="fact", importance=5),))
