"""Unit tests: compaction (memory flush, summarize, bounded history).

The compact node is exercised through the real chat graph with a FakeLLM
that answers the compaction prompt deterministically.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ChatGraph
from iris.agent.compaction import messages_tokens, trim_messages
from iris.agent.runtime import Runtime
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
from iris.onboarding import OnboardingWizard
from iris.sandbox import Sandbox


# ── trim_messages: pair integrity ─────────────────────────────────────────

def _history() -> list:
    return [
        HumanMessage(content="hi"),
        AIMessage(content="hello!", tool_calls=[{"id": "c1", "name": "memory_search", "args": {"query": "x"}}]),
        ToolMessage(content='{"ok": true}', tool_call_id="c1"),
        AIMessage(content="found it"),
        HumanMessage(content="tell me about it"),
        AIMessage(content="sure, here it is"),
        HumanMessage(content="thanks"),
        AIMessage(content="anytime"),
    ]


def test_trim_budget_respected():
    kept = trim_messages(_history(), keep_tokens=10)
    assert len(kept) < len(_history())
    assert messages_tokens(kept) <= 10 + 8  # one-message slack for the boundary


def test_trim_never_orphans_tool_results():
    for budget in (4, 8, 12, 16, 20, 30, 50):
        kept = trim_messages(_history(), keep_tokens=budget)
        first = kept[0]
        assert first.type != "tool", f"budget {budget}: history starts with an orphan tool result"
        if first.type == "ai" and getattr(first, "tool_calls", None):
            # every kept ai-with-calls must have its tool results after it
            for m in kept[1:]:
                assert m.type == "tool", f"budget {budget}: tool results missing after call"


def test_trim_keeps_everything_within_budget():
    kept = trim_messages(_history(), keep_tokens=10_000)
    assert len(kept) == len(_history())


# ── compact node through the real graph ───────────────────────────────────

class CompactLLM(LLMClient):
    """Answers the compaction prompt with canned facts+summary; plain turns
    reply 'ok' without tool calls."""

    def __init__(self) -> None:
        self.compaction_calls = 0

    async def complete(self, messages, **kwargs):
        system = messages[0].get("content", "")
        if "compaction pass" in system:
            self.compaction_calls += 1
            return '{"facts": ["Owner likes hiking.", "Owner is learning Spanish."], "summary": "We discussed hobbies and language learning."}'
        return "ok"

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
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
        sandbox=Sandbox(files.root / "sandbox"),
    )


def _onboard(files: WorkspaceFiles) -> None:
    w = OnboardingWizard(files)
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        w.apply_answer(a)


async def test_compaction_triggers_and_flushes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "compaction_trigger_tokens", 10)
    monkeypatch.setattr(settings, "compaction_keep_tokens", 8)
    files = WorkspaceFiles(tmp_path)
    _onboard(files)
    llm = CompactLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    for i in range(6):
        await graph.respond(f"message number {i} about hiking and plans", session_id="s1")

    assert llm.compaction_calls >= 1, "compaction prompt must have run"
    note = files.daily_note().read_text(encoding="utf-8")
    assert "Compaction flush" in note
    assert "Owner likes hiking." in note


async def test_compaction_summary_injected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "compaction_trigger_tokens", 10)
    monkeypatch.setattr(settings, "compaction_keep_tokens", 8)
    files = WorkspaceFiles(tmp_path)
    _onboard(files)
    llm = CompactLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    for i in range(6):
        await graph.respond(f"message number {i}", session_id="s2")

    state = await graph.graph.aget_state({"configurable": {"thread_id": "s2"}})
    assert state.values.get("conversation_summary", "") == "We discussed hobbies and language learning."
    # history must stay bounded: at most a few turns past the last compaction
    assert len(state.values["messages"]) <= 6
    assert messages_tokens(state.values["messages"]) < 3 * settings.compaction_trigger_tokens


async def test_compaction_survives_llm_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """If the flush call fails, the turn still completes and trims happen."""

    from iris.config import settings

    monkeypatch.setattr(settings, "compaction_trigger_tokens", 10)
    monkeypatch.setattr(settings, "compaction_keep_tokens", 8)

    class FailingLLM(LLMClient):
        async def complete(self, messages, **kwargs):
            raise RuntimeError("provider down")

        async def complete_with_tools(self, messages, tools=None, **kwargs):
            return "still alive", [], ""

    files = WorkspaceFiles(tmp_path)
    _onboard(files)
    graph = ChatGraph(make_runtime(files, FailingLLM()), MemorySaver())

    for i in range(6):
        reply = await graph.respond(f"message number {i}", session_id="s3")
    assert reply == "still alive"

    state = await graph.graph.aget_state({"configurable": {"thread_id": "s3"}})
    assert len(state.values["messages"]) <= 6
    assert state.values.get("conversation_summary", "") == ""


async def test_compaction_never_fires_on_short_chats(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    _onboard(files)
    llm = CompactLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    await graph.respond("hello", session_id="s4")
    await graph.respond("how are you", session_id="s4")
    assert llm.compaction_calls == 0