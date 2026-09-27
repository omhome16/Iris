"""The guard chain wired into the graph: refusals happen before dispatch."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from fakes import WizardLLM, skill_registry
from iris_ai import turnlog
from iris_ai.agent.chat import ChatGraph
from iris_ai.agent.runtime import Runtime
from iris_ai.budget import Budget, BudgetPolicy
from iris_ai.config import settings
from iris_ai.guards import GuardChain
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.llm import LLMClient
from iris_ai.onboarding import OnboardingWizard
from iris_ai.sandbox import Sandbox


async def _onboard(files: WorkspaceFiles) -> None:
    """The agent path is only reached once the workspace is onboarded."""
    wizard = OnboardingWizard(files, WizardLLM())
    for answer in ["Omar", "warm", "short", "UTC", "4"]:
        await wizard.apply_answer(answer)


class RepeatingLLM(LLMClient):
    """Emits the same tool call every step — the loop the guards exist for."""

    def __init__(self, tool: str = "memory_search", args: dict | None = None, times: int = 10) -> None:
        self.tool = tool
        self.args = args or {"query": "same"}
        self.times = times
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls <= self.times:
            return "", [{"name": self.tool, "args": dict(self.args)}], ""
        return "answered from what I had", [], ""

    async def complete(self, messages, **kwargs):
        """A memory_search turn is retrieval-backed, so the journal's reflection
        pass calls this. Without it the fake inherits the real provider call —
        a unit test that reaches the network, and the source of the suite's only
        `RuntimeWarning` (an unawaited LiteLLM coroutine the closed loop drops).
        The verdict is a canned "nothing flagged": this test is about the guard
        chain, not about triage."""
        return '{"flagged": []}'


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


def _runtime(files: WorkspaceFiles, llm: LLMClient, guards: GuardChain | None = None) -> Runtime:
    runtime = Runtime(
        files=files,
        llm=llm,
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(files.root / "sandbox"),
    )
    runtime.guards = guards
    return runtime


async def test_a_spiralling_call_is_refused_before_dispatch(tmp_path: Path, monkeypatch):
    """The claim is not \"the loop was observed\" but \"the loop was refused\":
    the third identical call never reaches the tool."""
    dispatched: list[str] = []

    async def spy(runtime, name, args, **kwargs):
        dispatched.append(name)
        return '{"ok": true}'

    monkeypatch.setattr("iris_ai.agent.chat.dispatch", spy)
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    runtime = _runtime(files, RepeatingLLM(times=10))
    graph = ChatGraph(runtime, MemorySaver())
    await graph.respond("go", session_id="loop")

    assert dispatched == ["memory_search", "memory_search"]  # only the first two got through
    assert graph.guards is not None


async def test_every_refusal_is_recorded_in_the_turn_trace(tmp_path: Path, monkeypatch):
    async def spy(runtime, name, args, **kwargs):
        return '{"ok": true}'

    monkeypatch.setattr("iris_ai.agent.chat.dispatch", spy)
    files = WorkspaceFiles(tmp_path)
    # Capture the turn log directly: `record()` only writes inside a turn.
    runtime = _runtime(files, RepeatingLLM(times=10))
    graph = ChatGraph(runtime, MemorySaver())

    with turnlog.collect() as turn:
        graph.guards.reset_turn()
        verdicts = [graph.guards.before("memory_search", {"query": "same"}) for _ in range(4)]
        for verdict in verdicts:
            graph.guards.record(verdict, "memory_search", {"query": "same"})

    events = [e for e in turn.judgments if e["kind"] == "tool_guard"]
    assert [e["event"] for e in events] == ["spiral", "spiral"]
    assert all(e["tool"] == "memory_search" for e in events)
    assert "near-identical" in events[0]["reason"]


async def test_a_healthy_turn_is_untouched_by_the_guards(tmp_path: Path, monkeypatch):
    """Guards must not tax the normal path: distinct calls pass silently."""
    dispatched: list[str] = []

    async def spy(runtime, name, args, **kwargs):
        dispatched.append(name)
        return '{"ok": true}'

    monkeypatch.setattr("iris_ai.agent.chat.dispatch", spy)
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    runtime = _runtime(files, RepeatingLLM(tool="file_read", args={"path": "notes.md"}, times=2))
    graph = ChatGraph(runtime, MemorySaver())
    await graph.respond("go", session_id="normal")
    assert dispatched == ["file_read", "file_read"]


async def test_a_tool_that_keeps_failing_opens_its_circuit(tmp_path: Path, monkeypatch):
    dispatched: list[str] = []

    async def failing(runtime, name, args, **kwargs):
        dispatched.append(name)
        return '{"ok": false, "error": "boom"}'

    monkeypatch.setattr("iris_ai.agent.chat.dispatch", failing)
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    runtime = _runtime(files, RepeatingLLM(tool="skill_run", args={"name": "x", "script": "s.py"}, times=10))
    graph = ChatGraph(runtime, MemorySaver())
    await graph.respond("go", session_id="failing")

    # two failures call the tool, the rest are refused by the circuit
    assert dispatched == ["skill_run", "skill_run"]


class SpendingLLM(LLMClient):
    """Reports usage through the client's own recording path, like a real call."""

    def __init__(self, prompt: int = 100, completion: int = 50, cached: int = 8) -> None:
        # No ledger: the accounting under test is the turn-scoped accumulator,
        # which deliberately does not depend on one being configured.
        super().__init__(None)
        self.prompt = prompt
        self.completion = completion
        self.cached = cached
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        from types import SimpleNamespace

        self._record(
            "fake-model",
            str(kwargs.get("tier", "strong")),
            SimpleNamespace(
                prompt_tokens=self.prompt,
                completion_tokens=self.completion,
                prompt_tokens_details=SimpleNamespace(cached_tokens=self.cached),
            ),
        )
        return "answered from what I had", [], ""

    async def complete(self, messages, **kwargs):
        return '{"flagged": []}'


async def test_a_finished_turn_banks_its_spend_into_the_day_budget(tmp_path: Path):
    """The day ceiling reads counters that only a finished turn writes.

    This is the wiring the ceiling depends on: nothing in `iris_ai.agent` used to
    call `Budget.note_usage`, so the cross-session bound read zero forever and
    could never refuse anything.
    """
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    budget = Budget(BudgetPolicy(max_tokens_per_day=10**9), path=tmp_path / "config" / "budget.json")
    guards = GuardChain.from_settings(budget)
    runtime = _runtime(files, SpendingLLM(), guards=guards)
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond("go", session_id="spend")

    counters = budget.snapshot()["counters"]
    assert counters["input"] == 100
    assert counters["output"] == 50
    assert counters["cached"] == 8  # the CACHED bucket has a source
    assert budget.path is not None and budget.path.exists()  # survives a restart
    assert guards.before("memory_search", {"query": "a"}).allowed  # plenty of room left


async def test_the_day_ceiling_stops_a_later_turn(tmp_path: Path):
    """End to end: what one turn spends refuses the *next* turn's tool calls.

    The turn-scoped ceiling cannot express this — a loop that spends a little
    every turn is exactly what a per-day ceiling is for.
    """
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    budget = Budget(BudgetPolicy(max_tokens_per_day=100), path=tmp_path / "config" / "budget.json")
    guards = GuardChain.from_settings(budget)
    runtime = _runtime(files, SpendingLLM(prompt=100, completion=50, cached=0), guards=guards)
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond("go", session_id="first")

    # A new turn: the turn counter starts at zero, and the day ceiling fires.
    verdict = graph.guards.before("memory_search", {"query": "next"})
    assert verdict.refused
    assert verdict.guard == "budget"
    assert "today's token ceiling" in verdict.reason
    assert guards is not None


def test_the_day_budget_survives_a_reset_between_turns(tmp_path: Path):
    budget = Budget(BudgetPolicy(max_tokens_per_day=50), path=tmp_path / "config" / "budget.json")
    guards = GuardChain.from_settings(budget)
    guards.reset_turn()
    assert guards.before("memory_search", {"query": "a"}).allowed
    budget.note_tokens("output", 50)
    guards.reset_turn()  # a new turn
    verdict = guards.before("memory_search", {"query": "b"})
    assert verdict.refused
    assert verdict.guard == "budget"


def test_guards_can_be_switched_off_in_settings(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "tool_guard_enabled", False)
    files = WorkspaceFiles(tmp_path)
    runtime = _runtime(files, RepeatingLLM(times=3))
    graph = ChatGraph(runtime, MemorySaver())
    with turnlog.collect():
        for _ in range(12):
            assert graph.guards.before("memory_search", {"query": "same"}).allowed
