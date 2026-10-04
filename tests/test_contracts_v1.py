"""iris/v1 types, the capability context, and the lock."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from iris_ai.components.lock import pin, read_lock, write_lock
from iris_ai.sdk.context import ComponentContext, WorkspaceAccess
from iris_ai.sdk.types import (
    Conflict,
    ConsolidationPlan,
    ContextBlock,
    ContextResult,
    MemoryCandidate,
    MemoryItem,
    dumps,
    memory_item_from,
    plan_from,
)
from iris_ai.testing import fake_context, memory_conformance


def test_every_v1_value_round_trips_through_json():
    item = MemoryItem(
        id="MEMORY.md::0",
        content="Owner prefers uv.",
        path="MEMORY.md",
        observed_at=date(2026, 6, 14),
        origin="owner",
        importance=7,
        why=(("relevance", 0.5),),
    )
    candidate = MemoryCandidate(content="use uv", kind="decision", importance=8, supersedes=item.id)
    plan = ConsolidationPlan(
        add=(candidate,),
        supersede=(item.id,),
        conflicts=(Conflict(existing=item, incoming=candidate, reason="two live facts"),),
        consume=("memory/2026-06-14.md",),
    )
    restored_item = memory_item_from(json.loads(dumps(item)))
    restored_plan = plan_from(json.loads(dumps(plan)))
    assert restored_item == item
    assert restored_plan.add[0].content == "use uv"
    assert restored_plan.conflicts[0].reason == "two live facts"


def test_default_prefix_render_matches_the_assembler_string():
    """The blocks join the way the prompt has always joined them."""
    result = ContextResult(
        blocks=(
            ContextBlock(title="Operating contract", text="Be brief.", kind="contract", priority=0),
            ContextBlock(title="Owner profile", text="Name: Ada", kind="profile", priority=10),
            ContextBlock(
                title="Long-term memory (curated)",
                text="- [7] Owner prefers uv.",
                kind="memory",
                priority=20,
            ),
            ContextBlock(title="Relevant skills", text="- web-page-to-notes: save a page", kind="skills"),
        ),
        skills=("web-page-to-notes",),
    )
    expected = (
        "## Operating contract\nBe brief.\n\n"
        "## Owner profile\nName: Ada\n\n"
        "## Long-term memory (curated)\n- [7] Owner prefers uv.\n\n"
        "## Relevant skills\n- web-page-to-notes: save a page"
    )
    assert result.render() == expected


def test_an_empty_title_renders_the_text_alone():
    assert ContextResult(blocks=(ContextBlock(title="", text="Answer briefly.", kind="custom"),)).render() == (
        "Answer briefly."
    )


def test_v1_context_has_no_runtime_and_refuses_env(tmp_path: Path):
    (tmp_path / ".env").write_text("SECRET=1\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "harness.toml").write_text("", encoding="utf-8")
    (tmp_path / "notes.md").write_text("hello\n", encoding="utf-8")
    files = WorkspaceAccess(tmp_path)
    ctx = fake_context("context", files=files)
    assert ctx.runtime is None
    assert ctx.llm is None
    assert ctx.memory is None
    assert files.read("notes.md") == "hello\n"
    with pytest.raises(PermissionError):
        files.read(".env")
    with pytest.raises(PermissionError):
        files.read("config/harness.toml")


@pytest.mark.asyncio
async def test_the_llm_cap_is_enforced():
    class LLM:
        async def complete(self, prompt: str, *, tier: str = "cheap") -> str:
            return "ok"

    from iris_ai.sdk.context import ModelAccess

    access = ModelAccess(LLM(), cap=1)
    assert await access.complete("hi") == "ok"
    with pytest.raises(RuntimeError, match="cap"):
        await access.complete("again")


def test_a_v1_lock_migrates_a_v1_file_and_refuses_drift(tmp_path: Path):
    path = tmp_path / "components.lock"
    path.write_text(json.dumps({"persona": {"active": "researcher", "previous": "file", "fails": 1}}), encoding="utf-8")
    data = read_lock(path)
    assert data["version"] == 2
    assert data["kinds"]["persona"]["active"] == "researcher"
    assert data["kinds"]["persona"]["digest"] == ""
    pin("context", "recall-first", source="builtin", digest="abc", path=path)
    assert read_lock(path)["kinds"]["context"]["digest"] == "abc"
    write_lock(read_lock(path), path)
    from iris_ai.components.lock import drifted

    assert drifted("context", digest="zzz", path=path) is True
    assert drifted("context", digest="abc", path=path) is False


def test_v0_context_was_removed():
    runtime = type("R", (), {"llm": "model", "index": "mem", "files": "files"})()
    with pytest.raises(TypeError, match=r"removed in 0\.6"):
        ComponentContext(runtime, {"top_k": 3})


@pytest.mark.asyncio
async def test_memory_conformance_names_a_missing_method():
    class Partial:
        async def search(self, query: str, **kwargs):
            return []

    missing = await memory_conformance(Partial())
    assert "nearest" in missing
    assert "search" not in missing
