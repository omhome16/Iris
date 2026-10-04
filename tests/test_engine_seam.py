"""The engine decides. The kernel checks the step before it acts."""

from __future__ import annotations

import pytest

from iris_ai.engines.react import ReactEngine
from iris_ai.kernel.runner import EngineRunner
from iris_ai.sdk.engine import FINISH, TOOLS, Step, check_step


class _Services:
    def __init__(self) -> None:
        self.model = type("M", (), {"issued": {"call_ok"}})()


def test_a_bad_next_is_refused():
    with pytest.raises(ValueError, match="unknown next"):
        check_step(Step(update={}, next="nope"), nodes={"agent"}, issued=set())


def test_tools_without_calls_is_refused():
    with pytest.raises(ValueError, match="tool calls"):
        check_step(
            Step(update={"messages": [{"type": "ai", "content": "hi"}]}, next=TOOLS, after="agent"),
            nodes={"agent"},
            issued=set(),
        )


def test_a_forged_tool_id_is_refused():
    with pytest.raises(ValueError, match="not minted"):
        check_step(
            Step(
                update={"messages": [{"type": "ai", "tool_calls": [{"id": "forged", "name": "x", "args": {}}]}]},
                next=TOOLS,
                after="agent",
            ),
            nodes={"agent"},
            issued={"call_ok"},
        )


def test_unserializable_scratch_is_refused():
    with pytest.raises(ValueError, match="JSON"):
        check_step(Step(update={"engine": {"bad": object()}}, next=FINISH), nodes={"agent"}, issued=set())


@pytest.mark.asyncio
async def test_react_finishes_a_text_reply():
    async def agent(state):
        return {"messages": [{"type": "ai", "content": "hello"}]}

    engine = ReactEngine(agent)
    step = await EngineRunner(engine, _Services()).run_node("agent", {})
    assert step.next == FINISH
    assert step.update["messages"][0]["content"] == "hello"


@pytest.mark.asyncio
async def test_a_toy_engine_reaches_tools_only_through_the_kernel_node():
    class Toy:
        name = "toy"
        entry = "think"
        nodes: dict

        def __init__(self) -> None:
            self.nodes = {"think": self.think}

        async def think(self, state, services):
            call_id = "call_ok"
            return Step(
                update={"messages": [{"type": "ai", "tool_calls": [{"id": call_id, "name": "memory_search", "args": {}}]}]},
                next=TOOLS,
                after="think",
            )

    step = await EngineRunner(Toy(), _Services()).run_node("think", {})
    assert step.next == TOOLS
    assert step.after == "think"


@pytest.mark.asyncio
async def test_the_runner_stops_at_the_recursion_cap(monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "graph_recursion_limit", 1)

    class Loop:
        name = "loop"
        entry = "a"
        nodes: dict

        def __init__(self) -> None:
            self.nodes = {"a": self.a}

        async def a(self, state, services):
            return Step(update={"messages": [{"type": "ai", "content": "x"}]}, next=FINISH)

    runner = EngineRunner(Loop(), _Services())
    await runner.run_node("a", {})
    with pytest.raises(RecursionError):
        await runner.run_node("a", {})
