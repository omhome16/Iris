"""Behaviour suites run the component. verify exits non-zero when list_chunks lies."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iris_ai.config import settings
from iris_ai.eval.world import open_memory
from iris_ai.testing import (
    HashEmbedder,
    context_conformance,
    memory_conformance,
    persona_conformance,
    suite_name,
)


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    (config / "harness.toml").write_text("[components]\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_config", str(config / "harness.toml"))


@pytest.mark.asyncio
async def test_sqlite_passes_the_memory_suite(tmp_path: Path) -> None:
    problems = await memory_conformance(open_memory(tmp_path, llm=HashEmbedder()))
    assert problems == []


@pytest.mark.asyncio
async def test_context_persona_and_channel_suites_call_the_component() -> None:
    from iris_ai.sdk.types import ContextResult
    from iris_ai.testing import channel_conformance, run_conformance

    class Context:
        def __init__(self, ctx=None, **options) -> None:
            self.ctx = ctx

        async def assemble(self, request):
            return ContextResult()

    class Persona:
        def text(self) -> str:
            return "Be direct."

    class Channel:
        async def send_message(self, chat_id, text) -> str:
            return f"{chat_id}:{text}"

    class Capture:
        async def extract(self, request):
            return []

    class Consolidator:
        async def propose(self, request):
            from iris_ai.sdk.types import ConsolidationPlan

            return ConsolidationPlan()

    from iris_ai.engines.dispatch import resolve
    from iris_ai.testing import capture_conformance, consolidator_conformance, engine_conformance

    assert await context_conformance(Context()) == []
    assert persona_conformance(Persona()) == []
    assert await channel_conformance(Channel()) == []
    assert await capture_conformance(Capture()) == []
    assert await consolidator_conformance(Consolidator()) == []
    assert engine_conformance(resolve("react")) == []
    assert suite_name("memory") == "memory-behaviour"
    assert await run_conformance("memory", Context(), suite="nope") == ["unknown suite nope"]


def test_verify_fails_when_list_chunks_is_empty_after_upsert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "memory" / "hollow"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "memory"\nname = "hollow"\napi_version = "iris/v1"\nentry = "component:Hollow"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Hollow:\n"
        "    def __init__(self, ctx=None, **options):\n"
        "        self.rows = []\n"
        "    async def connect(self):\n"
        "        return None\n"
        "    async def close(self):\n"
        "        return None\n"
        "    async def search(self, query, **kwargs):\n"
        "        return list(self.rows)\n"
        "    async def upsert_chunks(self, records):\n"
        "        self.rows = list(records)\n"
        "    async def replace_file_chunks(self, path, records):\n"
        "        self.rows = list(records)\n"
        "    async def delete_file_chunks(self, path):\n"
        "        self.rows = []\n"
        "    async def stats(self):\n"
        "        return {'total_chunks': len(self.rows)}\n"
        "    async def nearest(self, text, **kwargs):\n"
        "        return []\n"
        "    async def list_chunks(self):\n"
        "        return []\n"
        "    async def forget_entry(self, path, chunk_index):\n"
        "        return None\n",
        encoding="utf-8",
    )
    from iris_ai.cli.components_cmd import run
    from iris_ai.plug import construct, load_class
    from iris_ai.testing import memory_conformance

    component = construct(load_class(folder), None, trust="local")
    problems = asyncio.run(memory_conformance(component))
    assert any("list_chunks" in item for item in problems)
    assert run("verify", "memory", "hollow") == 1


def test_verify_passes_a_context_scaffold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    from iris_ai.cli.components_cmd import run
    from iris_ai.plug import scaffold

    scaffold("context", "plain", root=tmp_path / "components")
    assert run("verify", "context", "plain") == 0
    assert run("verify", "context", "plain", suite="context-behaviour") == 0
