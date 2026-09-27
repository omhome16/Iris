"""Deferred tools, loading them, and the catalog that makes them findable.

Deferral trades schema bytes for discoverability, so the two halves have to be
tested together: the surface is what the model can *call*, and the catalog is what
the model knows *exists*. A deferred capability nobody can ask for is a capability
that was silently deleted.

The tests below cover the two invariants that make loading safe and cheap:

- loading can never widen permission (a denied tool is not in the pool), and
- loading never disturbs the visible head, so the provider's cached prefix
  survives a change to the loaded set.
"""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from fakes import skill_registry
from iris_ai import turnlog
from iris_ai.agent.chat import ChatGraph
from iris_ai.agent.runtime import Runtime
from iris_ai.agent.tools import get_tools, tool_schemas, tool_surface
from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.skills import Skill
from iris_ai.sandbox import Sandbox
from iris_ai.toolpolicy import (
    LOADER_TOOL,
    NAMESPACE_PURPOSE,
    NAMESPACES,
    TOOL_DECLARATIONS,
    deferred_catalog,
    namespace_gaps,
    namespace_of,
)

# ── the namespace table is total, in both directions ────────────────────────


def test_the_namespace_table_partitions_every_declared_tool():
    """A namespace table that misses a tool drops it from the catalog — the
    exact invisibility the catalog exists to fix. So the table has to be total."""
    assert namespace_gaps() == ([], [])


def test_no_tool_is_declared_in_two_namespaces():
    """`namespace_of` returns the *first* match, so a duplicate would hide the
    second copy from any check that only looks at membership."""
    classified = [name for members in NAMESPACES.values() for name in members]
    assert len(classified) == len(set(classified))


def test_every_namespace_has_a_purpose_line():
    assert set(NAMESPACES) == set(NAMESPACE_PURPOSE)


def test_namespace_of_an_unknown_tool_is_empty_not_an_error():
    assert namespace_of("not_a_tool") == ""
    assert namespace_of("memory_search") == "memory"


# ── the catalog ─────────────────────────────────────────────────────────────


def test_an_empty_catalog_renders_nothing():
    """A prompt section that says "nothing here" is pure per-turn cost."""
    assert deferred_catalog([]) == ""
    assert deferred_catalog(["not_a_tool"]) == ""


def test_the_catalog_names_only_what_was_deferred():
    catalog = deferred_catalog(["send_message", "web_search"])
    assert "send_message" in catalog and "web_search" in catalog
    assert "memory_search" not in catalog
    assert "## Tools you can load (2 not shown)" in catalog


def test_the_catalog_groups_by_namespace_one_line_each():
    catalog = deferred_catalog(["send_message", "web_search", "send_photo"])
    groups = [line for line in catalog.splitlines() if line.startswith("- ")]
    assert len(groups) == 2  # delivery (two members) + web (one)
    assert next(line for line in groups if line.startswith("- delivery")).count(",") == 1


def test_the_catalog_points_at_the_loader_tool():
    assert LOADER_TOOL in deferred_catalog(["send_message"])


def test_a_namespace_without_a_purpose_still_lists_its_tools(monkeypatch):
    """Dropping the tools would recreate the invisibility the catalog fixes, so
    the purpose line is degraded rather than the membership lost."""
    monkeypatch.delitem(NAMESPACE_PURPOSE, "web")
    catalog = deferred_catalog(["web_search"])
    assert "web_search" in catalog
    assert "- web:" in catalog


# ── the surface, and loading onto it ────────────────────────────────────────

class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


class StubLLM:
    async def complete(self, *args, **kwargs):
        return ""

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""

    async def embed(self, texts):
        return [[0.1]] * len(texts)


def _runtime(tmp_path: Path) -> Runtime:
    files = WorkspaceFiles(tmp_path)
    return Runtime(
        files=files,
        llm=StubLLM(),  # type: ignore[arg-type]
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(files.root / "sandbox"),
    )


def _names(schemas: list[dict]) -> list[str]:
    return [s["function"]["name"] for s in schemas]


def _core_budget(runtime: Runtime, monkeypatch) -> None:
    """A budget that defers the whole extended set, whatever this boot registers."""
    core = sum(
        1
        for t in get_tools(runtime)
        if TOOL_DECLARATIONS[t.name].surface == "core"
    )
    monkeypatch.setattr(settings, "tool_surface_budget", core)


def test_loading_appends_the_schema_at_the_tail(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    visible, catalog = tool_surface(runtime)
    assert "dream_now" in catalog
    assert "dream_now" not in _names(visible)

    loaded_schemas, _catalog = tool_surface(runtime, loaded=["dream_now"])
    assert _names(loaded_schemas) == [*_names(visible), "dream_now"]


def test_loading_leaves_the_visible_head_byte_identical(tmp_path, monkeypatch):
    """The deferral only pays off if the prefix is stable enough to cache, so a
    change to the loaded set must not reorder or re-render the head."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    head, _ = tool_surface(runtime)
    after, _ = tool_surface(runtime, loaded=["dream_now", "skill_revise"])
    assert after[: len(head)] == head


def test_a_loaded_tool_drops_out_of_the_catalog(tmp_path, monkeypatch):
    """The catalog is what the model must go *find*; repeating a tool it is
    already holding is telling it to search twice."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    _, before = tool_surface(runtime)
    _, after = tool_surface(runtime, loaded=["dream_now"])
    assert "dream_now" in before
    assert "dream_now" not in after


def test_a_policy_denied_tool_is_never_loaded(tmp_path, monkeypatch):
    """Deferral is presentation; permission is not. A denied tool is filtered
    *before* the pool, so loading cannot reach it."""
    monkeypatch.setattr(settings, "tool_policy_overrides", "skill_write=deny")
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    schemas, catalog = tool_surface(runtime, loaded=["skill_write"])
    assert "skill_write" not in _names(schemas)
    assert "skill_write" not in catalog


async def test_find_tools_reports_and_loads_what_it_matched(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    tool = next(t for t in get_tools(runtime) if t.name == "find_tools")
    with turnlog.collect() as log:
        result = await tool.handler(query="dream_now")
    payload = json.loads(result)
    assert payload["ok"] is True
    assert payload["loaded"] == ["dream_now"]
    assert _names(payload["tools"]) == ["dream_now"]
    entry = next(e for e in log.judgments if e.get("event") == "find_tools")
    assert entry["loaded"] == ["dream_now"]


async def test_find_tools_without_a_node_runs_and_reports(tmp_path, monkeypatch):
    """A direct dispatch (no tool node) still answers — loading is a no-op, but
    the model still gets the schemas it asked for."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    tool = next(t for t in get_tools(runtime) if t.name == "find_tools")
    payload = json.loads(await tool.handler(query=""))
    assert payload["ok"] is True
    assert "dream_now" in payload["loaded"]
    # An unqueried call sees the whole deferral, so the two counts must account
    # for every registered tool: visible + deferred == the surface.
    assert payload["visible"] + payload["deferred"] == len(get_tools(runtime))


# ── the tools node owns the state write ─────────────────────────────────────

class _Message:
    """The minimum an AI message needs for the tool node to consume it."""

    def __init__(self, *calls: tuple[str, dict]) -> None:
        self.type = "ai"
        self.content = ""
        self.tool_calls = [
            {"name": name, "args": args, "id": f"call_{i}"} for i, (name, args) in enumerate(calls)
        ]


async def test_the_tools_node_accumulates_loaded_tools(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    graph = ChatGraph(runtime, MemorySaver())
    update = await graph._tools(
        {  # type: ignore[typeddict-item]
            "messages": [_Message(("find_tools", {"query": "dream_now"}))],
            "session_id": "s",
            "origin": "owner",
        }
    )
    assert update["loaded_tools"] == ("dream_now",)


async def test_loading_is_accumulated_not_replaced(tmp_path, monkeypatch):
    """What has been looked at once need not be looked for again — including
    after a resume, which is why it lives in state rather than in a local."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    graph = ChatGraph(runtime, MemorySaver())
    update = await graph._tools(
        {  # type: ignore[typeddict-item]
            "messages": [_Message(("find_tools", {"query": "skill_revise"}))],
            "session_id": "s",
            "origin": "owner",
            "loaded_tools": ("dream_now",),
        }
    )
    assert update["loaded_tools"] == ("dream_now", "skill_revise")


async def test_a_turn_that_loads_nothing_leaves_state_untouched(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    graph = ChatGraph(runtime, MemorySaver())
    update = await graph._tools(
        {  # type: ignore[typeddict-item]
            "messages": [_Message(("file_list", {}))],
            "session_id": "s",
            "origin": "owner",
        }
    )
    assert "loaded_tools" not in update


async def test_find_tools_offers_only_what_the_surface_will_load(tmp_path, monkeypatch):
    """The tool and the surface must agree: `find_tools` reports a tool as loaded
    only if the *narrowed* surface will actually put its schema in the prompt.
    Otherwise the model is told it holds a tool that is not there and calls it
    blind — the exact guessing this mechanism exists to prevent."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    # The skill has to allow `find_tools` for the call to happen at all (the
    # allowlist narrows this turn too), and deliberately excludes `dream_now`.
    runtime.skills.write(
        Skill(
            name="reader",
            description="d",
            procedure="p",
            allowed_tools=["find_tools", "inspect_mind"],
        )
    )
    graph = ChatGraph(runtime, MemorySaver())
    update = await graph._tools(
        {  # type: ignore[typeddict-item]
            "messages": [_Message(("find_tools", {"query": "dream_now"}))],
            "session_id": "s",
            "origin": "owner",
            "active_skills": ("reader",),
        }
    )
    payload = json.loads(update["messages"][0]["content"])
    assert payload["loaded"] == []
    assert "dream_now" not in payload["loaded"]


async def test_find_tools_still_loads_what_the_skill_did_allow(tmp_path, monkeypatch):
    """Narrowing is not a blanket refusal: a skill that allows the tool must be
    able to load it, and the surface must then actually append it."""
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(settings, "tool_surface_budget", 1)
    runtime.skills.write(
        Skill(
            name="writer",
            description="d",
            procedure="p",
            allowed_tools=["find_tools", "skill_revise"],
        )
    )
    graph = ChatGraph(runtime, MemorySaver())
    update = await graph._tools(
        {  # type: ignore[typeddict-item]
            "messages": [_Message(("find_tools", {"query": "skill_revise"}))],
            "session_id": "s",
            "origin": "owner",
            "active_skills": ("writer",),
        }
    )
    payload = json.loads(update["messages"][0]["content"])
    assert payload["loaded"] == ["skill_revise"]
    surfaced = _names(
        tool_surface(runtime, "owner", ("writer",), payload["loaded"])[0]
    )
    assert "skill_revise" in surfaced


async def test_a_non_owner_find_tools_cannot_offer_a_blocked_tool(tmp_path, monkeypatch):
    """A scheduled task cannot `remember`; asking to load it must not say it did."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    graph = ChatGraph(runtime, MemorySaver())
    update = await graph._tools(
        {  # type: ignore[typeddict-item]
            "messages": [_Message(("find_tools", {"query": "skill_write"}))],
            "session_id": "s",
            "origin": "task",
        }
    )
    payload = json.loads(update["messages"][0]["content"])
    assert payload["loaded"] == []


def test_tool_schemas_and_tool_surface_cannot_disagree(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    assert tool_schemas(runtime, loaded=["dream_now"]) == tool_surface(
        runtime, loaded=["dream_now"]
    )[0]


def test_the_catalog_never_names_a_tool_that_is_on_the_surface(tmp_path, monkeypatch):
    """The two halves of "what the model can reach" must not drift: a tool it
    already holds a schema for is not something it should go look for."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    visible, catalog = tool_surface(runtime)
    for name in _names(visible):
        if name == LOADER_TOOL:
            continue  # the loader is named in the catalog's instructions, on purpose
        assert name not in catalog


def test_a_non_owner_session_is_not_advertised_a_blocked_tool(tmp_path, monkeypatch):
    """A scheduled task cannot `remember`, so the catalog must not advertise it —
    the same origin split the surface applies, applied to the description too."""
    runtime = _runtime(tmp_path)
    _core_budget(runtime, monkeypatch)
    schemas, catalog = tool_surface(runtime, "task")
    assert "remember" not in _names(schemas)
    assert "remember" not in catalog
