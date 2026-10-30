"""Supersede. The new line retires the old one.

A later preference that shares a subject with a curated line is not folded
into that line and not stored beside it. The plan names the old line in
`supersede` and carries the new line in `add`. There is no merged sentence.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"[A-Za-z]{4,}")
_MOVED = re.compile(r"\b(moved|instead|prefer|switched)\b", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    return {word.lower() for word in _TOKEN.findall(text)}


class Supersede:
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
        retired = [
            line
            for line in curated.splitlines()
            if line.strip() and _MOVED.search(incoming) and len(_tokens(line) & _tokens(incoming)) >= 1
        ]
        return ConsolidationPlan(
            add=(MemoryCandidate(content=incoming, kind="fact", importance=6),),
            supersede=tuple(retired),
        )
