"""An added fact that contradicts a live one becomes a conflict, not a second fact."""

from __future__ import annotations

import re


def contradicts(existing: str, incoming: str) -> bool:
    """A later preference that shares a subject with a live line."""
    words = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", existing)}
    incoming_words = {word.lower() for word in re.findall(r"[A-Za-z]{4,}", incoming)}
    shared = words & incoming_words
    moved = bool(re.search(r"\b(moved|instead|now|prefer)\b", incoming, re.I))
    return moved and len(shared) >= 1


class ConflictResolver:
    api_version = "iris/v1"
    permissions = ("memory.read",)

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx

    async def propose(self, request):
        from datetime import date

        from iris_ai.sdk.types import Conflict, ConsolidationPlan, MemoryCandidate, MemoryItem

        prior = getattr(request, "prior", None)
        incoming = list(getattr(prior, "add", ()) or ())
        curated = str(getattr(request, "curated", "") or "")
        conflicts = []
        add = []
        for candidate in incoming:
            line = next((row for row in curated.splitlines() if contradicts(row, candidate.content)), "")
            if line:
                conflicts.append(
                    Conflict(
                        existing=MemoryItem(
                            id="MEMORY.md::0",
                            content=line.lstrip("- ").strip(),
                            path="MEMORY.md",
                            observed_at=date.today(),
                            origin="owner",
                        ),
                        incoming=candidate,
                        reason="two live facts about the same subject",
                    )
                )
            else:
                add.append(candidate)
        if not incoming and curated:
            # No prior plan: scan the notes the request carried.
            notes = str(getattr(request, "notes", "") or "")
            for line in notes.splitlines():
                text = line.strip()
                if text and contradicts(curated, text):
                    conflicts.append(
                        Conflict(
                            existing=MemoryItem(
                                id="MEMORY.md::0",
                                content=curated.splitlines()[0][:160],
                                path="MEMORY.md",
                                observed_at=date.today(),
                                origin="owner",
                            ),
                            incoming=MemoryCandidate(content=text, kind="preference"),
                            reason="two live facts about the same subject",
                        )
                    )
        return ConsolidationPlan(add=tuple(add), conflicts=tuple(conflicts))
