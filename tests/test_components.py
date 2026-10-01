"""Swappable context and memory, and the scaffold that writes one."""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.cli.scaffold import write_component
from iris_ai.components import OffCapture, OffConsolidator, attach, load_symbol
from iris_ai.kernel.threads import MemoryThreadStore


class _Runtime:
    def __init__(self) -> None:
        self.context_builder = None
        self.capture_policy = None
        self.dreams = "built-in"


def test_default_components_leave_the_runtime_alone():
    runtime = _Runtime()
    attach(runtime, {"components": {"context": "default", "capture": "default", "consolidator": "dreaming"}})
    assert runtime.context_builder is None
    assert runtime.capture_policy is None
    assert runtime.dreams == "built-in"


def test_off_disables_capture_and_consolidation():
    runtime = _Runtime()
    attach(runtime, {"components": {"capture": "off", "consolidator": "off"}})
    assert isinstance(runtime.capture_policy, OffCapture)
    assert isinstance(runtime.dreams, OffConsolidator)


def test_a_dotted_path_is_constructed_with_the_runtime():
    runtime = _Runtime()
    attach(runtime, {"components": {"context": "examples.custom_context.context:RagFirstContext"}})
    assert runtime.context_builder.__class__.__name__ == "RagFirstContext"


def test_scaffold_writes_a_context_file(tmp_path: Path):
    path = write_component("context", tmp_path / "context" / "component.py")
    assert path.is_file()
    assert "assemble_turn" in path.read_text(encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_component("context", path)


def test_load_symbol_rejects_a_bare_name():
    with pytest.raises(ValueError):
        load_symbol("not-a-path")


@pytest.mark.asyncio
async def test_a_thread_remembers_a_pending_approval():
    store = MemoryThreadStore()
    await store.save("cli", {"messages": [], "__interrupt__": [{"value": "delete the file?"}]})
    assert await store.pending_approval("cli") == "delete the file?"
    assert await store.pending_approval("missing") is None
