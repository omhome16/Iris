"""The turn loop. One file, no graph framework.

`ChatGraph` still owns the nodes (assemble, agent, tools, journal, capture).
This module loads a thread, runs the nodes in order, stops for an approval,
and saves the thread.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from iris_ai.kernel.events import Event, NodeUpdate, event_from_custom
from iris_ai.kernel.messages import merge_messages
from iris_ai.kernel.pause import GraphInterrupt, reset_stream_writer, set_resume_decision, set_stream_writer
from iris_ai.kernel.threads import ThreadStore, coerce_store


class GraphRecursionError(RuntimeError):
    """The turn took more node steps than `recursion_limit` allows."""


class Command:
    """Resume a paused turn. `resume` is the owner's decision string."""

    def __init__(self, resume: str | None = None) -> None:
        self.resume = resume


class Interrupt:
    def __init__(self, value: dict) -> None:
        self.value = value


def _blank(thread_id: str) -> dict:
    return {
        "messages": [],
        "memory_context": "",
        "conversation_summary": "",
        "stream": False,
        "session_id": thread_id,
        "origin": "owner",
        "last_capture": "",
        "active_skills": (),
        "loaded_tools": (),
        "tool_failures": "",
    }


def _apply(state: dict, update: dict | None) -> None:
    if not update:
        return
    for key, value in update.items():
        if key == "messages":
            state["messages"] = merge_messages(state.get("messages") or [], value)
        else:
            state[key] = value


class NativeGraph:
    """The object `ChatGraph.respond` calls. Same methods the tests patch."""

    def __init__(self, chat: Any, store: Any) -> None:
        self.chat = chat
        self.store: ThreadStore = coerce_store(store)

    async def ainvoke(self, incoming: Any, config: dict) -> dict:
        thread_id = config["configurable"]["thread_id"]
        limit = int(config.get("recursion_limit") or 40)
        state = await self.store.load(thread_id)
        if not state:
            state = _blank(thread_id)
        state["session_id"] = thread_id

        if isinstance(incoming, Command):
            set_resume_decision(None if incoming.resume is None else str(incoming.resume))
            state.pop("__interrupt__", None)
            node = "tools"
        else:
            set_resume_decision(None)
            for key, value in incoming.items():
                if key == "messages":
                    state["messages"] = merge_messages(state.get("messages") or [], value)
                else:
                    state[key] = value
            node = await self.chat._route(state)

        steps = 0
        while node not in ("end", None):
            steps += 1
            if steps > limit:
                await self.store.save(thread_id, state)
                raise GraphRecursionError(f"turn exceeded {limit} steps")
            if node == "onboarding":
                _apply(state, await self.chat._onboarding(state))
                node = "end"
            elif node == "assemble_context":
                _apply(state, await self.chat._assemble(state))
                node = self.chat._after_assemble(state)
            elif node == "compact":
                _apply(state, await self.chat._compact(state))
                node = "agent"
            elif node == "agent":
                _apply(state, await self.chat._agent(state))
                node = self.chat._after_agent(state)
            elif node == "tools":
                try:
                    _apply(state, await self.chat._tools(state))
                except GraphInterrupt as paused:
                    state["__interrupt__"] = [Interrupt(paused.value)]
                    await self.store.save(thread_id, state)
                    set_resume_decision(None)
                    return state
                node = self.chat._after_tools(state)
            elif node == "journal":
                _apply(state, await self.chat._journal(state))
                node = "capture"
            elif node == "capture":
                _apply(state, await self.chat._capture(state))
                node = "end"
            else:
                raise RuntimeError(f"unknown turn node {node!r}")
        state.pop("__interrupt__", None)
        await self.store.save(thread_id, state)
        set_resume_decision(None)
        return state

    async def astream(self, incoming: dict, config: dict, stream_mode: list[str] | None = None):
        """Yield legacy `(mode, payload)` pairs, each also a typed event."""
        collected: list[Event] = []

        def writer(payload: dict) -> None:
            collected.append(event_from_custom(payload))

        token = set_stream_writer(writer)
        thread_id = config["configurable"]["thread_id"]
        limit = int(config.get("recursion_limit") or 40)
        try:
            state = await self.store.load(thread_id) or _blank(thread_id)
            state["session_id"] = thread_id
            if isinstance(incoming, Command):
                set_resume_decision(None if incoming.resume is None else str(incoming.resume))
                state.pop("__interrupt__", None)
                node = "tools"
            else:
                set_resume_decision(None)
                for key, value in incoming.items():
                    if key == "messages":
                        state["messages"] = merge_messages(state.get("messages") or [], value)
                    else:
                        state[key] = value
                node = await self.chat._route(state)
            steps = 0
            while node not in ("end", None):
                steps += 1
                if steps > limit:
                    await self.store.save(thread_id, state)
                    raise GraphRecursionError(f"turn exceeded {limit} steps")
                before = len(collected)
                update: dict = {}
                if node == "onboarding":
                    update = await self.chat._onboarding(state) or {}
                    _apply(state, update)
                    nxt = "end"
                elif node == "assemble_context":
                    update = await self.chat._assemble(state) or {}
                    _apply(state, update)
                    nxt = self.chat._after_assemble(state)
                elif node == "compact":
                    update = await self.chat._compact(state) or {}
                    _apply(state, update)
                    nxt = "agent"
                elif node == "agent":
                    update = await self.chat._agent(state) or {}
                    _apply(state, update)
                    nxt = self.chat._after_agent(state)
                elif node == "tools":
                    try:
                        update = await self.chat._tools(state) or {}
                    except GraphInterrupt as paused:
                        state["__interrupt__"] = [Interrupt(paused.value)]
                        await self.store.save(thread_id, state)
                        yield NodeUpdate("tools", {})
                        return
                    _apply(state, update)
                    nxt = self.chat._after_tools(state)
                elif node == "journal":
                    update = await self.chat._journal(state) or {}
                    _apply(state, update)
                    nxt = "capture"
                elif node == "capture":
                    update = await self.chat._capture(state) or {}
                    _apply(state, update)
                    nxt = "end"
                else:
                    raise RuntimeError(f"unknown turn node {node!r}")
                for event in collected[before:]:
                    yield event
                yield NodeUpdate(node, update)
                node = nxt
            state.pop("__interrupt__", None)
            await self.store.save(thread_id, state)
        finally:
            reset_stream_writer(token)

    async def aget_state(self, config: dict):
        thread_id = config["configurable"]["thread_id"]
        state = await self.store.load(thread_id)
        pending = bool(state.get("__interrupt__"))
        return SimpleNamespace(values=state, next=("tools",) if pending else (), tasks=())


class TurnLoop:
    """The public turn API: `run` for a user message, `resume` after an approval."""

    def __init__(self, chat: Any, store: Any) -> None:
        self.graph = NativeGraph(chat, store)

    async def run(self, thread_id: str, message: str, **extra: Any):
        payload = {"messages": [{"role": "user", "content": message}], **extra}
        async for event in self.graph.astream(payload, {"configurable": {"thread_id": thread_id}}):
            yield event

    async def resume(self, thread_id: str, decision: str):
        async for event in self.graph.astream(
            Command(resume=decision), {"configurable": {"thread_id": thread_id}}
        ):
            yield event
