"""Research subagent: bounded cheap-tier digging + deep_dive tool + the
temporal-question research hook in context assembly."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ChatGraph
from iris.agent.subagents import ResearchSubagent
from iris.agent.tools import get_tools
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.onboarding import OnboardingWizard

from test_agent_graph import make_runtime


class ResearchLLM(LLMClient):
    """Round 1 searches escalation lane; round 2 reports."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "memory_search", "args": {"query": "beach trip", "top_k": 5}}], ""
        return "Report: the beach trip was in August.", [], ""

    async def complete(self, messages, **kwargs):
        return "{}"


class LoopLLM(ResearchLLM):
    """Never stops calling tools — the round cap must stop it."""

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "", [{"name": "memory_search", "args": {"query": "more"}}], ""


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


async def test_research_runs_and_returns_report(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    llm = ResearchLLM()
    sub = ResearchSubagent(make_runtime(files, llm))
    report = await sub.research("when did we go to the beach?")
    assert report == "Report: the beach trip was in August."
    assert llm.calls == 2


async def test_research_bounded_when_llm_loops(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    llm = LoopLLM()
    sub = ResearchSubagent(make_runtime(files, llm))
    report = await sub.research("keep digging")
    assert llm.calls <= 4  # 3 tool rounds + 1 cap round
    assert isinstance(report, str)


def test_deep_dive_tool_is_registered(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, ResearchLLM())
    names = {t.name for t in get_tools(runtime)}
    assert "deep_dive" in names


async def test_temporal_question_injects_research_into_context(tmp_path: Path, monkeypatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "mrr_top_k", 3)
    monkeypatch.setattr(settings, "trigger_inject_max", 3)
    files = WorkspaceFiles(tmp_path)
    _onboard(files)

    class MainLLM(LLMClient):
        """Research subagent (calls 1-2) then the main agent (call 3+).
        Records every system prompt it saw."""

        def __init__(self) -> None:
            self.calls = 0
            self.systems: list[str] = []

        async def complete_with_tools(self, messages, tools=None, **kwargs):
            self.calls += 1
            self.systems.append(messages[0]["content"])
            if self.calls == 1:
                return "", [{"name": "memory_search", "args": {"query": "beach trip", "top_k": 5}}], ""
            if self.calls == 2:
                return "Report: the beach trip was in August.", [], ""
            return "ok", [], ""

    llm = MainLLM()
    runtime = make_runtime(files, llm)
    runtime.index = StubIndex()  # type: ignore[assignment]
    runtime.research = ResearchSubagent(runtime)
    graph = ChatGraph(runtime, MemorySaver())

    reply = await graph.respond("when did we go to the beach?", session_id="r1")
    assert reply == "ok"
    assert any("Deep research report" in s and "beach trip was in August" in s for s in llm.systems)


def _onboard(files: WorkspaceFiles) -> None:
    w = OnboardingWizard(files)
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        w.apply_answer(a)