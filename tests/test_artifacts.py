"""Approved bytes are stored by digest, and a turn keeps the set it started with."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_ai.artifacts.store import executable_folder, ingest, verify
from iris_ai.components.lock import pin, read_lock
from iris_ai.generation import generation_id, swap_components
from iris_ai.lifecycle.journal import read_events, record
from iris_ai.plug import component_digest


def _component(folder: Path, body: str) -> None:
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "persona"\nname = "voice"\napi_version = "iris/v1"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(body, encoding="utf-8")


def test_ingest_verifies_and_a_tampered_byte_does_not(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\n", encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(harness))
    folder = tmp_path / "components" / "persona" / "voice"
    _component(folder, "class Component:\n    def text(self):\n        return 'a'\n")
    digest = ingest(folder)
    assert verify(digest)
    assert component_digest(folder) == digest
    stored = tmp_path / "components" / ".store" / f"sha256-{digest}" / "component.py"
    stored.write_text("class Component:\n    def text(self):\n        return 'tampered'\n", encoding="utf-8")
    assert verify(digest) is False


def test_a_pinned_component_loads_from_the_store(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\n", encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(harness))
    folder = tmp_path / "components" / "persona" / "voice"
    _component(folder, "class Component:\n    def text(self):\n        return 'a'\n")
    digest = ingest(folder)
    pin("persona", "voice", source="local", digest=digest, path=harness.parent / "components.lock")
    loaded = executable_folder("persona", "voice")
    assert loaded is not None
    assert loaded.name == f"sha256-{digest}"
    assert "return 'a'" in (loaded / "component.py").read_text(encoding="utf-8")
    (folder / "component.py").write_text("class Component:\n    def text(self):\n        return 'edited'\n", encoding="utf-8")
    loaded = executable_folder("persona", "voice")
    assert loaded is not None
    assert loaded.name == f"sha256-{digest}"
    assert "return 'a'" in (loaded / "component.py").read_text(encoding="utf-8")
    assert read_lock()["kinds"]["persona"]["digest"] == digest
    from iris_ai.components import _refuse_drift

    assert _refuse_drift("persona", "voice") == ""


def test_lifecycle_journal_appends(tmp_path: Path):
    path = tmp_path / "lifecycle.jsonl"
    record("activate", path=path, kind="persona", name="voice", digest="abc")
    record("rollback", path=path, kind="persona", name="file")
    events = read_events(path)
    assert [row["event"] for row in events] == ["activate", "rollback"]
    assert events[0]["digest"] == "abc"


def test_swap_assigns_the_generation_only_after_attach(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\ncontext = \"default\"\n", encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(harness))
    runtime = SimpleNamespace(context_builder="old", capture_policy=None, dreams="built-in", persona_choice="file")
    section = {"context": "default", "capture": "default", "consolidator": "dreaming"}
    notice = swap_components(runtime, {"components": section})
    assert notice == ""
    assert runtime.context_builder == "old"
    assert runtime.generation_id == generation_id(section)


def test_a_failed_swap_leaves_the_runtime(monkeypatch):
    def boom(scratch, manifest):
        del manifest
        scratch.context_builder = "new"
        raise RuntimeError("nope")

    monkeypatch.setattr("iris_ai.components.attach", boom)
    runtime = SimpleNamespace(context_builder="old")
    with pytest.raises(RuntimeError, match="nope"):
        swap_components(runtime, {"components": {}})
    assert runtime.context_builder == "old"
    assert not hasattr(runtime, "generation_id")


def test_list_local_ignores_the_store(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\n", encoding="utf-8")
    from iris_ai.config import settings
    from iris_ai.plug import list_local

    monkeypatch.setattr(settings, "harness_config", str(harness))
    folder = tmp_path / "components" / "persona" / "voice"
    _component(folder, "class Component:\n    def text(self):\n        return 'a'\n")
    ingest(folder)
    names = [item.name for item in list_local()]
    assert names == ["voice"]


def test_a_turn_keeps_the_assembler_it_started_with():
    from iris_ai.agent.chat import ChatGraph

    class Asm:
        def __init__(self, label: str) -> None:
            self.label = label

        async def assemble(self, request):
            del request
            return SimpleNamespace(render=lambda: self.label, skills=())

    runtime = SimpleNamespace(pipelines={}, generation_id="gen-1")
    graph = ChatGraph.__new__(ChatGraph)
    graph.runtime = runtime
    graph.assembler = Asm("first")
    graph._turn_assembler = None
    graph._turn_generation = ""
    graph.bind_turn()
    graph.assembler = Asm("second")

    class Msg:
        content = "hi"

    state = {"messages": [Msg()], "session_id": "s", "origin": "owner"}
    update = asyncio.run(graph._assemble(state))
    assert update["memory_context"] == "first"
    graph.release_turn()
    update = asyncio.run(graph._assemble(state))
    assert update["memory_context"] == "second"
