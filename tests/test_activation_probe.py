"""Activation and the jailed probe agree on an empty memory.

`iris components use` verifies the folder. The matrix used to be the only
path that handed a context component a real index, so a component that
searched memory died on activation and still passed the matrix.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from iris_ai.capabilities.memory_api import REQUIRED
from iris_ai.components.lock import read_lock
from iris_ai.config import settings
from iris_ai.plug import _MEMORY_REQUIRED


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    harness = config / "harness.toml"
    harness.write_text("[components]\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_config", str(harness))


def _copy_shipped(tmp_path: Path, kind: str, name: str) -> Path:
    source = Path(__file__).resolve().parents[1] / "components" / kind / name
    dest = tmp_path / "components" / kind / name
    shutil.copytree(source, dest)
    return dest


def test_memory_required_is_a_literal_copy_of_the_protocol() -> None:
    """The jailed check must not import the memory package to read this tuple."""
    assert _MEMORY_REQUIRED == REQUIRED


def test_verification_index_is_connected_and_closed(tmp_path: Path) -> None:
    from iris_ai.memory.sqlite_index import SqliteIndex
    from iris_ai.plug import _close_capability, _verification_index
    from iris_ai.testing import HashEmbedder

    index = _verification_index(tmp_path, HashEmbedder())
    assert isinstance(index, SqliteIndex)
    assert index._conn is not None
    _close_capability(index)
    assert index._conn is None


def test_probe_searches_empty_memory_and_completes_without_a_provider() -> None:
    from iris_ai.plug import _probe_v1, load_class, shipped_folder

    for name in ("recall-first", "compaction-first"):
        folder = shipped_folder("context", name)
        assert folder is not None
        detail = _probe_v1("context", load_class(folder))
        assert detail == "", detail


def test_use_activates_recall_first_and_compaction_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    from iris_ai.cli.components_cmd import run

    for name in ("recall-first", "compaction-first"):
        _copy_shipped(tmp_path, "context", name)
        assert run("use", "context", name) == 0
        row = read_lock()["kinds"]["context"]
        assert row["active"] == name
        assert row["source"] == "local"
        assert row["suite"] == "context-behaviour"
        assert row["verified_at"]
        assert row["digest"]
        assert "memory.read" in row["permissions"]


def test_scaffolded_memory_passes_the_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    from iris_ai.plug import check_in_sandbox, scaffold

    folder = scaffold("memory", "floor", root=tmp_path / "components")
    ok, detail = check_in_sandbox(folder)
    assert ok, detail


def test_jailed_check_does_not_import_numpy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    from iris_ai.plug import check_in_sandbox, scaffold

    folder = scaffold("memory", "quiet", root=tmp_path / "components")
    (folder / "test_component.py").write_text(
        "import sys\n"
        "from pathlib import Path\n"
        "from iris_ai.plug import check_folder\n"
        "ok, detail = check_folder(Path(__file__).parent)\n"
        "assert ok, detail\n"
        "for name in ('numpy', 'iris_ai.memory.index', 'iris_ai.capabilities.memory'):\n"
        "    assert name not in sys.modules, name\n",
        encoding="utf-8",
    )
    ok, detail = check_in_sandbox(folder)
    assert ok, detail


def test_jailed_probe_stands_in_for_memory_and_the_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    from iris_ai.plug import check_in_sandbox

    folder = tmp_path / "components" / "context" / "asks"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "context"\nname = "asks"\napi_version = "iris/v1"\n'
        'entry = "component:Asks"\npermissions = ["memory.read", "llm"]\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Asks:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "        self.options = options\n"
        "    async def assemble(self, request):\n"
        "        from iris_ai.sdk.types import ContextResult\n"
        "        hits = await self.ctx.memory.search(getattr(request, 'message', ''), top_k=3)\n"
        "        text = await self.ctx.llm.complete('say nothing')\n"
        "        if hits != [] or text != '':\n"
        "            raise AssertionError((hits, text))\n"
        "        return ContextResult()\n",
        encoding="utf-8",
    )
    ok, detail = check_in_sandbox(folder)
    assert ok, detail
