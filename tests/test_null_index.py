"""`NullIndex` — the degraded stand-in used when Postgres is unreachable.

A degraded session must never look like an empty memory: every *recall* method
raises `MemoryUnavailable` with the fix command in the message, while the
derived-index write methods are accepted no-ops so Markdown evidence still
accrues for the next time the database is up.
"""

from __future__ import annotations

import pytest

from iris_ai.memory.index import MemoryUnavailable
from iris_ai.memory.null_index import NullIndex

DSN = "postgresql+psycopg://iris:iris_dev_password@localhost:5433/iris"


@pytest.fixture
def index() -> NullIndex:
    return NullIndex(DSN)


async def test_recall_methods_raise_with_the_fix_command(index: NullIndex):
    for call in (
        lambda: index.search("tea"),
        lambda: index.escalate("tea"),
        lambda: index.nearest("tea"),
        lambda: index.list_chunks(),
    ):
        with pytest.raises(MemoryUnavailable) as exc:
            await call()
        assert "docker compose up -d postgres" in str(exc.value)
        assert "5433" in str(exc.value)


async def test_stats_reports_degraded(index: NullIndex):
    """Same shape as the real `stats()` plus the degraded markers."""
    stats = await index.stats()
    assert stats["degraded"] is True
    assert stats["total_chunks"] == 0
    assert stats["by_origin"] == {}
    assert "postgres" in stats["reason"].lower()


async def test_lifecycle_and_writes_are_noops(index: NullIndex):
    """Writes must not raise: the index is derived, and Markdown is the truth."""
    await index.connect()
    index.clear_cache()
    await index.upsert_chunks([])
    await index.replace_file_chunks("memory/2026-09-23.md", [])
    await index.delete_file_chunks("memory/2026-09-23.md")
    await index.forget_entry("memory/2026-09-23.md", 0)
    await index.close()
