"""`/forget` — the two-phase route the Telegram bridge depends on.

Regression under test: the route read `h.chunk_index` from a `MemoryHit` that
never carried one, so it raised `AttributeError` (a 500) and the bridge's
forget flow could never complete — `/forget-confirm` needs that index.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from iris.api import ForgetRequest, app, forget_search
from iris.memory.index import MemoryHit
from iris.memory.provenance import Origin


def _hit(*, chunk_index: int, content: str = "owner prefers green tea") -> MemoryHit:
    return MemoryHit(
        content=content,
        path="MEMORY.md",
        score=0.9,
        importance=7.0,
        origin=Origin.OWNER,
        observed_at=date.today(),
        evergreen=True,
        chunk_index=chunk_index,
    )


class _FakeIndex:
    """Minimal stand-in: the route only calls `search`."""

    def __init__(self, hits: list[MemoryHit]) -> None:
        self._hits = hits

    async def search(self, query: str, **kwargs) -> list[MemoryHit]:
        return self._hits


@pytest.fixture
def runtime_with(monkeypatch: pytest.MonkeyPatch):
    def _install(hits: list[MemoryHit]) -> None:
        monkeypatch.setattr(app.state, "runtime", SimpleNamespace(index=_FakeIndex(hits)), raising=False)

    return _install


async def test_forget_returns_candidates_with_chunk_index(runtime_with):
    runtime_with([_hit(chunk_index=3)])
    data = await forget_search(ForgetRequest(query="tea"), _token=None)
    assert [c["chunk_index"] for c in data["candidates"]] == [3]
    assert data["candidates"][0]["path"] == "MEMORY.md"
    assert data["candidates"][0]["content"] == "owner prefers green tea"


async def test_forget_skips_hits_without_a_chunk_index(runtime_with):
    """A hit the index cannot locate is skipped, not a crash."""
    runtime_with([_hit(chunk_index=-1), _hit(chunk_index=2, content="other")])
    data = await forget_search(ForgetRequest(query="tea"), _token=None)
    assert [c["chunk_index"] for c in data["candidates"]] == [2]


async def test_forget_ignores_non_curated_paths(runtime_with):
    """Only MEMORY.md is editable; daily notes are append-only."""
    daily = MemoryHit(
        content="went to the beach",
        path="memory/2026-09-23.md",
        score=0.8,
        importance=3.0,
        origin=Origin.AGENT,
        observed_at=date.today(),
        evergreen=False,
        chunk_index=1,
    )
    runtime_with([daily])
    data = await forget_search(ForgetRequest(query="beach"), _token=None)
    assert data["candidates"] == []
