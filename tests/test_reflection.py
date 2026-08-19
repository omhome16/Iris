"""Reflection pass: retrieval-gated hallucination triage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ChatGraph
from iris.memory.llm import LLMClient
from iris.memory.reflection import ReflectionPass, retrieved_excerpts

from test_agent_graph import make_runtime


class RetrieveThenReflectLLM(LLMClient):
    """Turn 1: memory_search tool call; turn 2: a confident (unverified) claim.

    complete() answers the write-path extraction prompt with empty memories
    and the reflection prompt with a canned verdict."""

    def __init__(self, flagged: list[dict]) -> None:
        self.calls = 0
        self.flagged = flagged

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "memory_search", "args": {"query": "lease"}}]
        return "Your lease renews on September 1st 2026.", []

    async def complete(self, messages, **kwargs):
        user = messages[-1]["content"] if messages else ""
        if "Retrieved memory excerpts" in user:
            return json.dumps({"flagged": self.flagged})
        return json.dumps({"memories": []})


class ExplodingReflectLLM(RetrieveThenReflectLLM):
    async def complete(self, messages, **kwargs):
        raise RuntimeError("provider down")


def test_retrieved_excerpts_collects_tool_messages_only():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "memory_search", "id": "c1", "args": {"query": "x"}}]),
            ToolMessage(content="hit 1", tool_call_id="c1"),
            AIMessage(content="answer", tool_calls=[]),
        ]
    }
    assert retrieved_excerpts(state) == ["hit 1"]


async def test_reflection_writes_flags_when_retrieval_happened(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([{"claim": "lease ends Sept 1", "why": "no excerpt supports the date"}])
    pass_ = ReflectionPass(llm, path)
    await pass_.check(
        user_message="when does my lease end?",
        ai_reply="Your lease renews on September 1st 2026.",
        retrieved=["the owner pays rent on the 1st"],
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert "lease ends Sept 1" in data["claim"]
    assert data["why"]


async def test_reflection_skipped_without_retrieval(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([{"claim": "x", "why": "y"}])
    pass_ = ReflectionPass(llm, path)
    await pass_.check(user_message="hi", ai_reply="hello", retrieved=[])
    assert not path.exists()


async def test_reflection_llm_failure_never_raises(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    pass_ = ReflectionPass(ExplodingReflectLLM([{"claim": "x", "why": "y"}]), path)
    await pass_.check(
        user_message="q", ai_reply="a", retrieved=["some excerpt"]
    )  # must not raise
    assert not path.exists()


async def test_reflection_no_flags_no_file(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([])
    pass_ = ReflectionPass(llm, path)
    await pass_.check(user_message="q", ai_reply="a", retrieved=["e"])
    assert not path.exists()


async def test_graph_turn_with_retrieval_writes_flag(tmp_path: Path):
    from iris.memory.files import WorkspaceFiles
    from iris.onboarding import OnboardingWizard

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files)
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        w.apply_answer(a)
    llm = RetrieveThenReflectLLM([{"claim": "lease ends Sept 1", "why": "unsupported"}])
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())
    reply = await graph.respond("when does my lease end?", session_id="t-flag")
    assert "September 1st" in reply

    flags = files.root / "config" / "hallucination_flags.jsonl"
    assert flags.exists()
    assert "lease ends Sept 1" in flags.read_text(encoding="utf-8")


async def test_graph_turn_without_retrieval_no_flag(tmp_path: Path):
    from test_agent_graph import FakeLLM

    from iris.memory.files import WorkspaceFiles
    from iris.onboarding import OnboardingWizard

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files)
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    await graph.respond("hi", session_id="t-noflag")
    assert not (files.root / "config" / "hallucination_flags.jsonl").exists()