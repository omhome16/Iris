"""The truth pass: listed names load, policy fails closed, a turn explains itself."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_ai.agent.chat import ChatGraph
from iris_ai.capabilities.memory import REQUIRED
from iris_ai.explain import render_trace
from iris_ai.hooks import HookBus
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.forgetting import ForgettingEngine
from iris_ai.memory.indexer import importance_of
from iris_ai.memory.provenance import Origin
from iris_ai.plug import entry_point_hint, scaffold


def test_importance_marker_is_read_from_a_bullet():
    assert importance_of("- [7] Owner prefers uv. (note)") == 7
    assert importance_of("no marker here") == 0.0


@pytest.mark.asyncio
async def test_reindex_keeps_the_importance_marker(tmp_path: Path):
    from iris_ai.memory.indexer import Reindexer

    class Index:
        def __init__(self) -> None:
            self.records: list = []

        async def replace_file_chunks(self, path: str, records: list) -> None:
            self.records = records

    files = WorkspaceFiles(tmp_path)
    index = Index()
    reindexer = Reindexer(files, index, llm=None)
    text = "- [7] Owner prefers uv for Iris examples. (note)\n"
    await reindexer._index_file("memory/2026-06-14.md", text, Origin.AGENT, False)
    assert index.records
    assert index.records[0].importance == 7


class _Exact:
    """A backend that implements REQUIRED and nothing else."""

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def search(self, query: str, **kwargs):
        return []

    async def escalate(self, query: str, **kwargs):
        return []

    async def stats(self) -> dict:
        return {"total_chunks": 0}

    async def upsert_chunks(self, records: list) -> None:
        return None

    async def delete_file_chunks(self, path: str) -> None:
        return None

    async def replace_file_chunks(self, path: str, records: list) -> None:
        return None

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        return None

    async def nearest(self, text: str, *, top_k: int = 3) -> list:
        return []

    async def list_chunks(self) -> list:
        return []


def test_exact_backend_declares_every_required_method():
    for name in REQUIRED:
        assert callable(getattr(_Exact(), name))


@pytest.mark.asyncio
async def test_exact_backend_survives_rot_and_dream_dedupe(tmp_path: Path):
    from iris_ai.memory.dreaming import DeepPhase

    backend = _Exact()
    assert await ForgettingEngine(backend).rot_report() == []
    assert await ForgettingEngine(backend).retention_report() == []
    files = WorkspaceFiles(tmp_path)
    assert await DeepPhase(files, backend)._duplicates("a fact", "something already written") is False
    assert await backend.list_chunks() == []


@pytest.mark.asyncio
async def test_untrusted_turn_with_a_custom_capture_writes_nothing(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    called = {"n": 0}

    class Policy:
        async def maybe_capture(self, *, user_message, reply, known_context):
            called["n"] += 1
            return "should not be stored"

    graph = ChatGraph.__new__(ChatGraph)
    graph.runtime = SimpleNamespace(files=files, capture_policy=Policy(), reindexer=None)
    state = {
        "origin": "untrusted",
        "messages": [
            SimpleNamespace(type="human", content="remember this"),
            SimpleNamespace(type="ai", content="ok", tool_calls=None),
        ],
    }
    assert await graph._capture(state) == {}
    assert called["n"] == 0
    assert "should not be stored" not in files.read_daily(files.today())


@pytest.mark.asyncio
async def test_a_raising_policy_hook_refuses_the_tool():
    bus = HookBus()

    def boom(tool: str, args: object) -> None:
        raise RuntimeError("nope")

    bus.on("pre_tool", boom, name="boom")
    refused = await bus.emit_policy("pre_tool", tool="memory_search", args={})
    assert refused[0].refused
    assert refused[0].guard == "hook:boom"
    assert await bus.emit("pre_tool", tool="memory_search", args={}) == []


def test_channel_is_not_a_folder_component(tmp_path: Path):
    with pytest.raises(ValueError, match="not a folder component"):
        scaffold("channel", "discord", root=tmp_path / "components")
    hint = entry_point_hint("channel")
    assert "iris_ai.channels" in hint
    assert "terminal" not in hint


def test_explain_names_the_harness_the_tools_and_the_cost():
    text = render_trace(
        {
            "model": "groq/test",
            "harness": {
                "engine": {"name": "react", "source": "builtin", "digest": ""},
                "context": {"name": "recall-first", "source": "builtin", "digest": ""},
            },
            "context_chars": 1240,
            "tools": [{"name": "memory_search", "policy_class": "read", "decision": "allow"}],
            "tokens": 80,
            "cost_usd": 0.000021,
        },
        path="config/traces.jsonl",
    )
    assert "groq/test" in text
    assert "recall-first" in text
    assert "1240 characters" in text
    assert "memory_search" in text
    assert "read" in text
    assert "allow" in text
    assert "80" in text
    assert "0.000021" in text
    assert "config/traces.jsonl" in text
