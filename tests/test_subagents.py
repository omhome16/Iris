"""Research subagent: bounded cheap-tier digging + deep_dive tool. The
subagent now runs only when the agent calls the deep_dive tool â€” context
assembly never auto-runs it (memory orchestration v2)."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from iris_ai.agent.chat import ChatGraph
from iris_ai.agent.subagents import ResearchSubagent
from iris_ai.agent.tools import get_tools
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.llm import LLMClient
from iris_ai.onboarding import OnboardingWizard
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
    """Never stops calling tools â€” the round cap must stop it."""

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


async def test_deep_dive_runs_only_when_agent_invokes_it(tmp_path: Path):
    """v2: the research subagent never auto-runs. The agent calls the
    deep_dive tool, gets the report as the tool result, and replies."""
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)

    class MainLLM(LLMClient):
        """Call 1 â†’ deep_dive tool; call 2 (inside the subagent) reports;
        call 3 answers."""

        def __init__(self) -> None:
            self.calls = 0
            self.systems: list[str] = []

        async def complete_with_tools(self, messages, tools=None, **kwargs):
            self.calls += 1
            self.systems.append(messages[0]["content"])
            if self.calls == 1:
                return "", [{"name": "deep_dive", "args": {"query": "beach trip"}}], ""
            if self.calls == 2:
                return "Report: the beach trip was in August.", [], ""
            return "ok", [], ""

        async def complete(self, messages, **kwargs):
            # The deep_dive result is retrieval evidence, so the journal's
            # reflection pass calls `complete` on this same double. Inheriting
            # `LLMClient.complete` would make a unit test attempt a real
            # provider call.
            return '{"flagged": []}'

    llm = MainLLM()
    runtime = make_runtime(files, llm)
    runtime.index = StubIndex()  # type: ignore[assignment]
    runtime.research = ResearchSubagent(runtime)
    graph = ChatGraph(runtime, MemorySaver())

    reply = await graph.respond("when did we go to the beach?", session_id="r1")
    assert reply == "ok"
    assert llm.calls == 3
    assert not any("Deep research report" in s for s in llm.systems), (
        "research must not be injected into the assembled context"
    )


async def test_no_auto_research_without_agent_invocation(tmp_path: Path):
    """A temporal question alone must not launch the subagent (v2: the
    agent decides; retrieval is a tool call, not a regex)."""
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)

    class QuietLLM(LLMClient):
        async def complete_with_tools(self, messages, tools=None, **kwargs):
            return "I don't remember that yet.", [], ""

        async def complete(self, messages, **kwargs):
            return '{"flagged": []}'

    llm = QuietLLM()
    runtime = make_runtime(files, llm)
    runtime.index = StubIndex()  # type: ignore[assignment]
    runtime.research = ResearchSubagent(runtime)
    graph = ChatGraph(runtime, MemorySaver())

    reply = await graph.respond("when did we go to the beach?", session_id="r2")
    assert reply == "I don't remember that yet."


async def _onboard(files: WorkspaceFiles) -> None:
    from fakes import WizardLLM

    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
