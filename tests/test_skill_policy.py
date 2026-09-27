"""The skill policy: an active skill may narrow a turn, never widen it.

`allowed-tools` has one direction only. A manifest that names a tool the runtime
has not registered is an error (the manifest is wrong), and a manifest can never
hand the agent a capability the session was already denied — a non-owner session
that activates a skill allowing `remember` still cannot write curated memory.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.agent.runtime import Runtime
from iris_ai.agent.tools import dispatch, tool_schemas
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.skills import Skill
from iris_ai.sandbox import Sandbox
from iris_ai.skills.policy import SkillPolicy
from iris_ai.skills.registry import SkillRegistry


def _skill(name: str, allowed: list[str] | None = None) -> Skill:
    return Skill(name=name, description="d", procedure="p", allowed_tools=allowed or [])


def test_no_active_skill_is_unrestricted():
    policy = SkillPolicy([])
    assert policy.restricted is False
    assert policy.check("anything").allowed is True


def test_a_skill_without_an_allowlist_is_unrestricted():
    """Every pre-P4 skill is in this state: it restricts nothing."""
    policy = SkillPolicy([_skill("svg-pro")])
    assert policy.restricted is False
    assert policy.check("memory_search").allowed is True


def test_a_non_empty_allowlist_narrows_the_turn():
    policy = SkillPolicy([_skill("pdf-notes", ["Read", "Write"])])
    assert policy.restricted is True
    assert policy.check("Read").allowed is True
    assert policy.check("memory_search").allowed is False


def test_a_denial_names_the_skill_and_what_it_allows():
    policy = SkillPolicy([_skill("pdf-notes", ["Read"])])
    decision = policy.check("dream_now")
    assert decision.allowed is False
    assert "pdf-notes" in decision.reason
    assert "Read" in decision.reason
    assert decision.skill == "pdf-notes"


def test_two_active_skills_are_a_union():
    policy = SkillPolicy([_skill("a", ["Read"]), _skill("b", ["Write"])])
    assert policy.check("Read").allowed is True
    assert policy.check("Write").allowed is True
    assert policy.check("dream_now").allowed is False


def test_an_empty_allowlist_does_not_block_a_restricted_sibling():
    policy = SkillPolicy([_skill("a", ["Read"]), _skill("b")])
    assert policy.check("Read").allowed is True


def test_schemas_are_narrowed_to_the_allowlist():
    schemas = [
        {"type": "function", "function": {"name": "Read", "description": "", "parameters": {}}},
        {"type": "function", "function": {"name": "web_search", "description": "", "parameters": {}}},
    ]
    kept = SkillPolicy([_skill("pdf-notes", ["Read"])]).filter_schemas(schemas)
    assert [s["function"]["name"] for s in kept] == ["Read"]
    # unrestricted → untouched
    assert SkillPolicy([]).filter_schemas(schemas) == schemas


# ── through the real dispatch path ──────────────────────────────────────────


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

    async def embed(self, texts):
        return [[0.1]] * len(texts)

    async def embed_one(self, text):
        return [0.1]


def _runtime_with(tmp_path: Path, *skills: Skill) -> Runtime:
    files = WorkspaceFiles(tmp_path)
    registry = SkillRegistry(files, builtin_dir=None, entry_points=lambda: [])
    for skill in skills:
        registry.write(skill)
    return Runtime(
        files=files,
        llm=StubLLM(),  # type: ignore[arg-type]
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=registry,
        sandbox=Sandbox(files.root / "sandbox"),
    )


async def test_dispatch_allows_a_tool_the_skill_lists(tmp_path):
    runtime = _runtime_with(tmp_path, _skill("reader", ["inspect_mind"]))
    out = await dispatch(runtime, "inspect_mind", {}, active_skills=["reader"])
    assert '"ok": true' in out


async def test_dispatch_refuses_a_tool_outside_the_skill(tmp_path):
    runtime = _runtime_with(tmp_path, _skill("reader", ["inspect_mind"]))
    out = await dispatch(runtime, "file_list", {}, active_skills=["reader"])
    assert '"ok": false' in out
    assert "reader" in out


async def test_dispatch_is_unrestricted_without_an_active_skill(tmp_path):
    runtime = _runtime_with(tmp_path, _skill("reader", ["inspect_mind"]))
    out = await dispatch(runtime, "file_list", {})
    assert '"ok": true' in out


async def test_dispatch_ignores_an_unknown_active_skill_name(tmp_path):
    """A stale session state naming a deleted skill must not lock the turn."""
    runtime = _runtime_with(tmp_path)
    out = await dispatch(runtime, "file_list", {}, active_skills=["no-such-skill"])
    assert '"ok": true' in out


async def test_a_skill_cannot_grant_a_non_owner_blocked_tool(tmp_path):
    """The two rules are independent: the skill allowlist cannot override the
    session rule (a scheduled task that activates a skill still cannot `remember`)."""
    runtime = _runtime_with(tmp_path, _skill("writer", ["remember"]))
    out = await dispatch(runtime, "remember", {"text": "x"}, origin="task", active_skills=["writer"])
    assert '"ok": false' in out
    assert "task" in out


async def test_a_denial_is_recorded_in_the_turn_trace(tmp_path):
    """A refusal nobody can see is indistinguishable from a tool that failed."""
    from iris_ai import turnlog

    runtime = _runtime_with(tmp_path, _skill("reader", ["inspect_mind"]))
    with turnlog.collect() as log:
        out = await dispatch(runtime, "file_list", {}, active_skills=["reader"])
    assert '"ok": false' in out
    denial = next(e for e in log.judgments if e["kind"] == "skill" and e.get("event") == "tool_denied")
    assert denial["tool"] == "file_list"
    assert denial["skill"] == "reader"


def test_tool_schemas_are_narrowed_for_the_model(tmp_path):
    runtime = _runtime_with(tmp_path, _skill("reader", ["inspect_mind"]))
    names = {s["function"]["name"] for s in tool_schemas(runtime, "owner", active_skills=["reader"])}
    assert names == {"inspect_mind"}
    assert len(tool_schemas(runtime)) > 1


def test_schemas_still_hide_non_owner_tools(tmp_path):
    runtime = _runtime_with(tmp_path)
    names = {s["function"]["name"] for s in tool_schemas(runtime, "task")}
    assert "remember" not in names and "memory_search" in names
