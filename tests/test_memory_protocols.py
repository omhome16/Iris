"""Core memory is seven methods. Maintenance and escalation are optional."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from iris_ai.capabilities.memory import (
    REQUIRED,
    MemoryBackend,
    MemoryEscalation,
    MemoryMaintenance,
    offers,
)
from iris_ai.config import settings
from iris_ai.memory.forgetting import ForgettingEngine
from iris_ai.memory.index import MemoryUnavailable
from iris_ai.memory.markdown_index import MarkdownIndex
from iris_ai.memory.null_index import NullIndex
from iris_ai.plug import check_folder


class CoreOnly:
    """A store that answers the seven core methods and nothing else."""

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def search(self, query: str, **kwargs):
        return []

    async def upsert_chunks(self, records: list) -> None:
        return None

    async def replace_file_chunks(self, path: str, records: list) -> None:
        return None

    async def delete_file_chunks(self, path: str) -> None:
        return None

    async def stats(self) -> dict:
        return {"total_chunks": 0}


def test_core_is_seven_methods_and_optional_protocols_are_separate() -> None:
    assert REQUIRED == (
        "connect",
        "close",
        "search",
        "upsert_chunks",
        "replace_file_chunks",
        "delete_file_chunks",
        "stats",
    )
    backend = CoreOnly()
    assert isinstance(backend, MemoryBackend)
    assert not isinstance(backend, MemoryMaintenance)
    assert not isinstance(backend, MemoryEscalation)
    assert offers(backend, MemoryMaintenance) is False
    assert offers(MarkdownIndex(), MemoryMaintenance) is False
    assert offers(MarkdownIndex(), MemoryEscalation) is True
    assert offers(NullIndex("postgresql://unused"), MemoryMaintenance) is True


@pytest.mark.asyncio
async def test_rot_over_a_core_store_is_empty_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    backend = CoreOnly()
    await backend.connect()
    with caplog.at_level(logging.INFO, logger="iris.memory"):
        report = await ForgettingEngine(backend).rot_report()
        retention = await ForgettingEngine(backend).retention_report()
    assert report == []
    assert retention == []
    assert sum("declines list_chunks" in record.message for record in caplog.records) == 2


@pytest.mark.asyncio
async def test_markdown_rot_does_not_reach_the_raising_stub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    index = MarkdownIndex()
    with caplog.at_level(logging.INFO, logger="iris.memory"):
        report = await ForgettingEngine(index).rot_report()
    assert report == []
    assert any("declines list_chunks" in record.message for record in caplog.records)
    from iris_ai.memory.dreaming import DeepPhase
    from iris_ai.memory.files import WorkspaceFiles

    files = WorkspaceFiles(tmp_path)
    with caplog.at_level(logging.INFO, logger="iris.memory"):
        assert await DeepPhase(files, index)._duplicates("a fact", "already written") is False
    assert any("declines nearest" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_the_degraded_index_still_raises_on_maintenance() -> None:
    index = NullIndex("postgresql+psycopg://iris:iris@127.0.0.1:1/iris")
    with pytest.raises(MemoryUnavailable):
        await ForgettingEngine(index).rot_report()


def test_a_memory_folder_with_only_search_fails_the_check(tmp_path: Path) -> None:
    folder = tmp_path / "thin"
    folder.mkdir()
    (folder / "component.toml").write_text(
        'kind = "memory"\nname = "thin"\nentry = "component:Thin"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Thin:\n    async def search(self, query, **kwargs):\n        return []\n",
        encoding="utf-8",
    )
    ok, detail = check_folder(folder)
    assert ok is False
    assert "connect" in detail
    assert "search" not in detail.split("must define", 1)[-1]
