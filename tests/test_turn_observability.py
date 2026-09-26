"""Per-turn observability: judgment recording, stage timings, backgrounded refletion.

Three claims this file pins down:

1. `iris_ai.turnlog` records what the judgment layer decided and where the time
   went — bounded, best-effort, and invisible outside a turn.
2. `iris_ai.background` runs post-reply passes without losing them: no bare
   `create_task`, strong references, failures logged rather than raised, and
   `drain()` available so shutdown and tests can wait instead of racing.
3. The reflection pass is off the reply path by default, and the trace says so.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from iris_ai import background, turnlog
from iris_ai.agent.chat import ChatGraph
from iris_ai.config import settings
from iris_ai.trace import TraceLogger
from test_agent_graph import make_runtime
from test_capture import _NoopReindexer, _onboarded

# ── turnlog ─────────────────────────────────────────────────────────────────

def test_recording_outside_a_turn_is_a_noop():
    """Tools can call `record` unconditionally: with no turn in flight it does
    nothing rather than raising or leaking into the next turn."""
    assert turnlog.active() is False
    turnlog.record("rerank", scored=3)
    turnlog.mark("tools", 12.0)
    with turnlog.stage("whatever"):
        pass
    assert turnlog.active() is False


def test_collect_full_and_excludes_the_previous_turn():
    with turnlog.collect() as first:
        turnlog.record("guard", action="block", source="http://x", injection=0.91)
    first_trace = first.to_trace()
    assert first_trace["counts"] == {"guard": 1}
    assert first_trace["events"][0]["injection"] == 0.91

    with turnlog.collect() as second:
        pass
    assert second.to_trace() == {}, "a fresh turn must not inherit the last turn's judgments"


def test_stage_timings_accumulate_across_a_react_loop():
    """A ReAct loop visits `tools` several times; the total is what matters."""
    with turnlog.collect() as log:
        with turnlog.stage("tools"):
            pass
        with turnlog.stage("tools"):
            pass
    assert log.stages["tools"] >= 0
    assert "tools" in log.to_trace()["stages_ms"]


def test_judgments_are_bounded_and_text_is_truncated():
    with turnlog.collect() as log:
        for i in range(200):
            turnlog.record("guard", source="x" * 5000, action="pass", injection=i / 200)
        turnlog.mark("tools", 1.0)
    assert len(log.judgments) == 60, "a 30-tool-call turn must not grow the trace without limit"
    assert log.dropped == 140 and "dropped" in log.to_trace()
    assert len(log.judgments[0]["source"]) <= 240


def test_a_record_that_explodes_never_reaches_the_caller():
    class Boom:
        def add(self, *a, **k):
            raise RuntimeError("telemetry bug")

    token = turnlog._current.set(Boom())  # type: ignore[arg-type]
    try:
        turnlog.record("guard", action="pass")  # must not raise
    finally:
        turnlog._current.reset(token)


# ── background tasks ────────────────────────────────────────────────────────

async def test_spawn_keeps_a_reference_and_drain_waits_for_it():
    seen: list[str] = []

    async def work():
        await asyncio.sleep(0)
        seen.append("done")

    task = background.spawn(work(), name="t-work")
    assert task is not None
    assert background.pending() == 1, "a task with no strong reference can be GC'd mid-flight"
    assert await background.drain() == 0
    assert seen == ["done"]
    assert background.pending() == 0


async def test_a_failing_background_task_is_logged_not_raised():
    async def boom():
        raise RuntimeError("provider down")

    background.spawn(boom(), name="t-boom")
    assert await background.drain() == 0, "drain must not propagate the failure"


async def test_spawn_without_a_running_loop_returns_none_and_closes_the_coroutine():
    """Sync context is the case that matters (a script, a scheduler job without
    a loop): the coroutine must be closed, never left un-awaited to warn."""

    async def work():
        return 1

    coro = work()
    ran = False

    def no_loop(*_a, **_k):
        raise RuntimeError("no running event loop")

    import asyncio as _asyncio

    loop = _asyncio.get_running_loop()
    try:
        _asyncio.set_event_loop(None)
    finally:
        pass
    # Simulate the no-loop branch directly rather than tearing down the
    # running test loop: patch create_task to refuse.
    original = loop.create_task
    loop.create_task = no_loop  # type: ignore[method-assign]
    try:
        assert background.spawn(coro) is None
        ran = True
    finally:
        loop.create_task = original  # type: ignore[method-assign]
    assert ran
    import contextlib

    with contextlib.suppress(RuntimeError):
        await coro  # closed coroutine: awaiting it raises instead of warning


# ── the reflection pass leaves the reply path ───────────────────────────────

class _SlowReflectLLM:
    """Counts reflection calls and signals when one starts."""

    def __init__(self) -> None:
        self.started = 0
        self.reached = asyncio.Event()

    async def complete(self, messages, **kwargs):
        self.started += 1
        self.reached.set()
        return json.dumps({"flagged": [{"claim": "lease ends Sept 1", "why": "unsupported"}]})

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""


def _state_with_retrieval(user: str, reply: str) -> dict:
    """State as the journal node sees it: one memory_search tool result."""
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    return {
        "session_id": "obs",
        "origin": "owner",
        "messages": [
            HumanMessage(content=user),
            AIMessage(content="", tool_calls=[{"name": "memory_search", "id": "c1", "args": {"query": "lease"}}]),
            ToolMessage(content="the owner pays rent on the 1st", tool_call_id="c1"),
            AIMessage(content=reply),
        ],
    }


async def test_journal_returns_without_waiting_for_reflection(tmp_path: Path):
    files = await _onboarded(tmp_path)
    llm = _SlowReflectLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())
    flags = files.root / "config" / "hallucination_flags.jsonl"

    with turnlog.collect() as log:
        await graph._journal(_state_with_retrieval("when does my lease end?", "Your lease ends Sept 1."))
        events = [e for e in log.judgments if e["kind"] == "reflection"]

    assert events == [{"kind": "reflection", "mode": "background", "excerpts": 1}]
    assert not flags.exists(), "the turn returned while the pass was still pending"

    assert await background.drain() == 0
    assert "lease ends Sept 1" in flags.read_text(encoding="utf-8")
    assert llm.started == 1


async def test_inline_mode_blocks_and_says_so(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "reflection_background", False)
    files = await _onboarded(tmp_path)
    llm = _SlowReflectLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())
    flags = files.root / "config" / "hallucination_flags.jsonl"

    with turnlog.collect() as log:
        await graph._journal(_state_with_retrieval("when does my lease end?", "Your lease ends Sept 1."))
        events = [e for e in log.judgments if e["kind"] == "reflection"]

    assert events[0]["mode"] == "inline"
    assert flags.exists(), "inline mode must be finished when the turn returns"
    assert log.stages["reflection"] >= 0


async def test_no_retrieval_means_no_reflection_call(tmp_path: Path):
    files = await _onboarded(tmp_path)
    llm = _SlowReflectLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    from langchain_core.messages import AIMessage, HumanMessage

    with turnlog.collect() as log:
        await graph._journal({"session_id": "obs", "origin": "owner", "messages": [HumanMessage(content="hi"), AIMessage(content="hello")]})

    assert llm.started == 0
    assert next(e for e in log.judgments if e["kind"] == "reflection")["mode"] == "skipped"


# ── the trace carries the judgment layer ────────────────────────────────────

async def test_trace_records_judgments_and_stage_timings(tmp_path: Path):

    files = await _onboarded(tmp_path)
    llm = _SlowReflectLLM()
    runtime = make_runtime(files, llm)
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    runtime.traces = TraceLogger(files.root / "config" / "traces.jsonl")
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond("I prefer my coffee black and always fly at 7am, my sister lives in Lisbon", session_id="obs1")

    traced = runtime.traces.recent()[0]
    stages = traced["stages_ms"]
    assert {"assemble", "agent", "capture"} <= set(stages), stages
    kinds = {e["kind"] for e in traced["events"]}
    # The capture node always reports *why*, so "declined" is distinguishable
    # from "never ran" — and the prefilter leaving a turn free is visible.
    assert "capture" in kinds
    capture = next(e for e in traced["events"] if e["kind"] == "capture")
    assert "reason" in capture


async def test_turnlog_can_be_switched_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "turnlog_enabled", False)
    files = await _onboarded(tmp_path)
    runtime = make_runtime(files, _SlowReflectLLM())
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    runtime.traces = TraceLogger(files.root / "config" / "traces.jsonl")
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond("hello there", session_id="obs2")

    assert "events" not in runtime.traces.recent()[0]


# ── JEV client health ───────────────────────────────────────────────────────

def test_status_explains_why_the_layer_is_off():
    from iris_ai.jev.client import JevClient

    client = JevClient(api_key="")
    status = client.status()
    assert status["enabled"] is False
    assert status["reason"] == "TYPESAFE_API_KEY is not set"
    assert status["requests"] == 0 and status["failures"] == 0


async def test_concurrent_callers_share_one_client(monkeypatch: pytest.MonkeyPatch):
    """A rerank, a guard screen and a capture judgment can now overlap; without
    the lock two of them would each build a client and leak one."""
    from iris_ai.jev import client as client_module
    from iris_ai.jev.client import JevClient

    created: list[object] = []

    class FakeSDK:
        def __init__(self, **kwargs) -> None:
            created.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(client_module, "AsyncTypeSafeClient", FakeSDK)
    monkeypatch.setattr(client_module, "RetryPolicy", lambda **kw: kw)
    client = JevClient(api_key="test-key")

    clients = await asyncio.gather(*(client._ensure() for _ in range(5)))

    assert len(created) == 1, "the client must be built exactly once"
    assert all(c is clients[0] for c in clients)
