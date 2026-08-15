"""Unit tests: onboarding wizard state machine + chat graph routing/tools.

Graph tests use MemorySaver (no Postgres) and a deterministic FakeLLM; the
onboarding path never touches the index, so these run without a DB or key.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ChatGraph, _to_llm_messages
from iris.agent.runtime import Runtime
from iris.agent.tools import get_tools
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
from iris.onboarding import OnboardingState, OnboardingWizard


# ── onboarding wizard ───────────────────────────────────────────────────────

def test_wizard_flow(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files)
    assert w.onboarded is False
    assert w.current_prompt() == "What should I call you?"

    assert w.apply_answer("Omar") == "How would you like me to be? (e.g. warm and curious, dry and efficient, playful)"
    assert w.apply_answer("warm and curious") == "Tone for messages? (e.g. short and direct, friendly and detailed)"
    assert w.apply_answer("short and direct") == "Your timezone? (e.g. Asia/Kolkata, UTC, America/New_York)"
    assert w.apply_answer("Asia/Kolkata") == "When should I run my nightly dream consolidation? (hour 0-23, e.g. 4)"
    reply = w.apply_answer("4")

    assert w.onboarded is True
    assert "Omar" in reply
    user_md = files.user.read_text(encoding="utf-8")
    assert "Name: Omar" in user_md
    assert "Asia/Kolkata" in user_md
    assert files.config_file().exists(), "state must persist to config/iris.json"


def test_wizard_persists_across_instances(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files)
    w.apply_answer("Aria")
    w2 = OnboardingWizard(files)  # fresh instance reads disk
    assert w2.state.owner_name == "Aria"
    assert w2.state.step == 1


def test_wizard_rejects_nothing_after_done(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files)
    for a in ["Aria", "calm", "detailed", "UTC", "2"]:
        w.apply_answer(a)
    assert w.onboarded
    assert "Aria" in w.current_prompt()  # welcome-back greeting


# ── chat graph ──────────────────────────────────────────────────────────────

class FakeLLM(LLMClient):
    """Deterministic: no tools, echo-ish reply. No network."""

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", []


class RememberLLM(LLMClient):
    """First call emits a remember tool call, second call answers."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "remember", "args": {"content": "Owner is testing tools.", "importance": 5}}]
        return "remembered", []


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


def make_runtime(files: WorkspaceFiles, llm: LLMClient) -> Runtime:
    return Runtime(
        files=files,
        llm=llm,
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=SkillLibrary(files),
    )


async def test_graph_routes_new_user_to_onboarding(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    reply = await graph.respond("Hi", session_id="t1")
    assert "call you" in reply  # first wizard question, message NOT consumed


async def test_graph_onboards_then_chats(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    for answer in ["Hi", "Omar", "warm", "short", "UTC", "4"]:
        reply = await graph.respond(answer, session_id="t2")
    # onboarded → next message routes to the ReAct loop, not the wizard
    reply = await graph.respond("hello there", session_id="t2")
    assert "welcome back" not in reply.lower()
    assert OnboardingWizard(files).onboarded is True


async def test_graph_tool_loop_calls_remember(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    # onboarded already
    w = OnboardingWizard(files)
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, RememberLLM()), MemorySaver())
    reply = await graph.respond("please remember this", session_id="t3")
    assert reply == "remembered"
    assert "Owner is testing tools." in files.memory.read_text(encoding="utf-8")


# ── message conversion ──────────────────────────────────────────────────────

def test_to_llm_messages_serializes_tool_args_as_json():
    class FakeMsg:
        type = "ai"
        content = ""
        tool_calls = [{"id": "call_1", "name": "remember", "args": {"content": "x"}}]

    out = _to_llm_messages([FakeMsg()])
    assert '"content": "x"' in out[0]["tool_calls"][0]["function"]["arguments"]


# ── tool registry ───────────────────────────────────────────────────────────

def test_tool_schemas_are_valid(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    runtime = Runtime(
        files=files,
        llm=FakeLLM(),
        index=None,  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=SkillLibrary(files),
    )
    schemas = [t.schema() for t in get_tools(runtime)]
    names = {s["function"]["name"] for s in schemas}
    assert names == {"memory_search", "remember", "inspect_mind", "forget",
                     "skill_write", "skill_list", "skill_apply", "dream_now"}
    for s in schemas:
        assert s["function"]["parameters"]["type"] == "object"