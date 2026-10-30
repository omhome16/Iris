"""Phase 0: one grant vocabulary, and a local memory folder gets a real context."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_ai.config import settings
from iris_ai.isolation.broker import Authority, expand_grants
from iris_ai.memory.files import WorkspaceFiles


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir(parents=True, exist_ok=True)
    harness = config / "harness.toml"
    harness.write_text("[components]\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_config", str(harness))


def test_short_grants_expand_to_the_same_set_in_both_hosts(tmp_path: Path) -> None:
    from iris_ai.plug import _v1_context

    declared = ("memory.read", "files.read", "state", "llm")

    class Component:
        permissions = declared
        component_kind = "context"
        component_name = "temporal-rag"

    files = WorkspaceFiles(tmp_path / "ws")
    runtime = SimpleNamespace(llm=object(), index=object(), files=files)
    ctx = _v1_context(Component, runtime, None)
    expanded = expand_grants(declared)
    assert ctx.grants == expanded
    assert Authority(declared).grants == expanded
    assert ctx.llm is not None
    assert ctx.memory is not None
    assert ctx.files is not None
    assert ctx.state is not None
    note = ctx.state.path("note.txt")
    note.write_text("ok", encoding="utf-8")
    assert note.read_text(encoding="utf-8") == "ok"
    assert Authority(["memory.read"]).allows("memory.search")
    assert Authority(["llm"]).allows("llm.embed")
    assert Authority(["state"]).allows("state.write")
    with pytest.raises(ValueError, match="unknown grant"):
        Authority(["memory.read", "net.request"])


def test_temporal_rag_loads_under_the_subprocess_host() -> None:
    from iris_ai.isolation.host import open_component

    folder = Path(__file__).resolve().parents[1] / "src" / "iris_ai" / "catalog" / "context" / "temporal-rag"
    hosted = open_component(
        folder,
        kind="context",
        name="temporal-rag",
        handlers={
            "memory.search": lambda params: [{"content": "I have moved to Rust", "path": "MEMORY.md"}],
            "memory.recent": lambda params: [],
            "memory.curated": lambda params: "",
        },
    )
    try:
        result = asyncio.run(
            hosted.assemble(SimpleNamespace(message="where did we move", session_id="s", origin="owner"))
        )
    finally:
        hosted.close()
    assert "Rust" in result.render()


class _Embedder:
    async def embed_one(self, text: str) -> list[float]:
        return [0.25, 0.5, float(len(text))]

    async def complete(self, prompt: str, *, tier: str = "cheap") -> str:
        return "noted"


def _memory_folder(root: Path) -> None:
    folder = root / "components" / "memory" / "demo-store"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "memory"\nname = "demo-store"\napi_version = "iris/v1"\n'
        'entry = "component:Store"\npermissions = ["llm", "state"]\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Store:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "        self.vector = None\n"
        "        self.where = None\n"
        "    async def connect(self):\n"
        "        self.vector = await self.ctx.llm.embed('hello')\n"
        "        self.where = self.ctx.state.path('note.txt')\n"
        "        self.where.write_text('ok', encoding='utf-8')\n"
        "        reply = await self.ctx.llm.complete('hi')\n"
        "        self.reply = reply\n"
        "    async def close(self):\n"
        "        return None\n"
        "    async def search(self, query, **kwargs):\n"
        "        return []\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_a_local_memory_folder_gets_a_real_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    _memory_folder(tmp_path)
    monkeypatch.setattr(settings, "memory_backend", "demo-store")
    from iris_ai.engine import _memory_index

    files = WorkspaceFiles(tmp_path / "ws")
    index = _memory_index(_Embedder(), None, None, files=files)
    await index.connect()
    assert index.vector == [0.25, 0.5, 5.0]
    assert index.reply == "noted"
    assert index.where.read_text(encoding="utf-8") == "ok"


def test_score_memory_executes_a_local_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "memory" / "ranker"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "memory"\nname = "ranker"\napi_version = "iris/v1"\nentry = "component:Ranker"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Ranker:\n"
        "    def __init__(self, ctx=None, **options):\n"
        "        self.rows = []\n"
        "    async def connect(self):\n"
        "        return None\n"
        "    async def upsert_chunks(self, records):\n"
        "        self.rows = list(records)\n"
        "    async def search(self, query, **kwargs):\n"
        "        return sorted(self.rows, key=lambda row: 1 if row.get('stale') else 0)\n",
        encoding="utf-8",
    )
    from iris_ai.eval.score import score_memory

    case = {
        "input": "AI work",
        "corpus": [
            {"id": "old", "content": "I prefer Python for AI work.", "stale": True},
            {"id": "new", "content": "I have moved to Rust for AI work.", "stale": False},
        ],
        "expect": {"relevant": ["new"]},
    }
    scored = score_memory("ranker", case)
    assert scored["stale_at_5"] == 0.0
    assert scored["recall_at_5"] == 1.0
    untouched = score_memory("not-a-folder", case)
    assert untouched["stale_at_5"] == 1.0


@pytest.mark.asyncio
async def test_reload_swaps_the_memory_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    files = WorkspaceFiles(tmp_path / "ws")
    from iris_ai.generation import finish_memory_swap, swap_components
    from iris_ai.memory.sqlite_index import SqliteIndex

    old = SqliteIndex(tmp_path / "old.db", llm=_Embedder())
    await old.connect()
    assert old._conn is not None
    runtime = SimpleNamespace(
        llm=_Embedder(),
        files=files,
        index=old,
        jev=None,
        harness_identity={"memory": {"name": "sqlite", "source": "builtin", "digest": ""}},
        context_builder="old",
        capture_policy=None,
        dreams=None,
        persona_choice="file",
        pipeline_fails={},
    )
    monkeypatch.setattr(settings, "memory_backend", "markdown")
    monkeypatch.setattr(settings, "workspace_dir", str(files.root))
    swap_components(runtime, {"components": {}})
    assert type(runtime.index).__name__ == "MarkdownIndex"
    assert runtime.index is not old
    assert runtime._pending_memory_connect is True
    await finish_memory_swap(runtime)
    assert runtime._pending_memory_connect is False
    assert old._conn is None
