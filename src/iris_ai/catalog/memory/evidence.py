"""Rank a current fact above a superseded one in the same subject.

This is the ranking rule evidence-memory adds at query time. The index still
stores both lines. Callers that already hold `MemoryHit`s can sort with
`current_first` without a second embedder.
"""

from __future__ import annotations

import re


def superseded(text: str) -> bool:
    return "(superseded" in text.lower()


_MOVED = re.compile(r"\b(moved|instead|switched)\b", re.IGNORECASE)
_PREFER = re.compile(r"\bprefer\b", re.IGNORECASE)


def _text(hit) -> str:
    if isinstance(hit, dict):
        return str(hit.get("content") or "")
    return str(getattr(hit, "content", "") or "")


def _score(hit) -> float:
    if isinstance(hit, dict):
        return float(hit.get("score") or 0)
    return float(getattr(hit, "score", 0) or 0)


def current_first(hits: list) -> list:
    """Current facts before superseded ones, and before an older preference.

    A line that says "prefer" is stale when another line in the same subject
    says the owner moved or switched. Location and project slots are not the
    only facts that go stale.
    """
    texts = [_text(hit) for hit in hits]
    moved: set[str] = set()
    for text in texts:
        if _MOVED.search(text):
            moved |= subject_tokens(text)

    def stale(text: str) -> bool:
        if superseded(text):
            return True
        return bool(_PREFER.search(text) and subject_tokens(text) & moved)

    def key(hit) -> tuple:
        return (1 if stale(_text(hit)) else 0, -_score(hit))

    return sorted(hits, key=key)


def subject_tokens(text: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z]{4,}", text)}


class EvidenceMemory:
    """SqliteIndex plus a ranking pass that keeps current facts ahead of stale ones."""

    def __init__(self, path: str = "config/memory.db", llm: object | None = None, reranker: object | None = None, **kwargs) -> None:
        from iris_ai.memory.sqlite_index import SqliteIndex

        self._inner = SqliteIndex(path, llm=llm, reranker=reranker, **kwargs)

    def __getattr__(self, name: str):
        return getattr(self._inner, name)

    async def search(self, query: str, **kwargs):
        hits = await self._inner.search(query, **kwargs)
        return current_first(list(hits))

    async def nearest(self, query: str, **kwargs):
        hits = await self._inner.nearest(query, **kwargs)
        return current_first(list(hits))
