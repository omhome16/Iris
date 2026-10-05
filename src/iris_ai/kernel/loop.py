"""The turn loop. One file, no graph framework.

`ChatGraph` still owns the nodes (assemble, agent, tools, journal, capture).
This module loads a thread, runs the nodes in order, stops for an approval,
and saves the thread.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from iris_ai.kernel.events import Event, NodeUpdate, Usage, event_from_custom
from iris_ai.kernel.messages import merge_messages
from iris_ai.kernel.pause import GraphInterrupt, reset_stream_writer, set_resume_decision, set_stream_writer
from iris_ai.kernel.threads import ThreadStore, coerce_store


class GraphRecursionError(RuntimeError):
    """The turn took more node steps than `recursion_limit` allows."""


class Command:
    """Resume a paused turn. `resume` is the owner's decision string."""

    def __init__(self, resume: str | None = None, bound: dict | None = None) -> None:
        self.resume = resume
        self.bound = bound


class Interrupt:
    def __init__(self, value: dict) -> None:
        self.value = value


def _usage_event() -> Usage | None:
    """Tokens and cost for the turn in flight. Nothing outside `turnlog.collect()`."""
    from iris_ai import turnlog
    from iris_ai.config import settings
    from iris_ai.ledger import estimate_cost

    log = turnlog.current()
    if log is None:
        return None
    prompt = sum(int(bucket.get("prompt_tokens", 0)) for bucket in log.usage.values())
    completion = log.completion_tokens()
    model = ""
    for names in log.models.values():
        if names:
            model = names[-1]
    if not model:
        model = settings.strong_model or settings.cheap_model or ""
    return Usage(
        tokens=log.total_tokens(),
        cost=round(estimate_cost(model, prompt, completion), 6),
        model=model,
    )


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
            set_resume_decision(
                None if incoming.resume is None else str(incoming.resume),
                bound=getattr(incoming, "bound", None),
            )
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
        self.chat.bind_turn()
        state["generation_id"] = getattr(self.chat, "_turn_generation", "")

        steps = 0
        paused = False
        try:
            while node not in ("end", None):
                steps += 1
                if steps > limit:
                    await self.store.save(thread_id, state)
                    raise GraphRecursionError(f"turn exceeded {limit} steps")
                try:
                    _update, node = await self._run_node(state, node)
                except GraphInterrupt as paused_turn:
                    paused = True
                    state["__interrupt__"] = [Interrupt(paused_turn.value)]
                    await self.store.save(thread_id, state)
                    set_resume_decision(None)
                    return state
            state.pop("__interrupt__", None)
            await self.store.save(thread_id, state)
            set_resume_decision(None)
            return state
        finally:
            if not paused:
                self.chat.release_turn()

    async def astream(self, incoming: dict, config: dict, stream_mode: list[str] | None = None):
        """Yield legacy `(mode, payload)` pairs, each also a typed event."""
        collected: list[Event] = []

        def writer(payload: dict) -> None:
            collected.append(event_from_custom(payload))

        token = set_stream_writer(writer)
        thread_id = config["configurable"]["thread_id"]
        limit = int(config.get("recursion_limit") or 40)
        paused = False
        try:
            state = await self.store.load(thread_id) or _blank(thread_id)
            state["session_id"] = thread_id
            if isinstance(incoming, Command):
                set_resume_decision(
                    None if incoming.resume is None else str(incoming.resume),
                    bound=getattr(incoming, "bound", None),
                )
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
            self.chat.bind_turn()
            state["generation_id"] = getattr(self.chat, "_turn_generation", "")
            steps = 0
            paused = False
            while node not in ("end", None):
                steps += 1
                if steps > limit:
                    await self.store.save(thread_id, state)
                    usage = _usage_event()
                    if usage is not None:
                        yield usage
                    raise GraphRecursionError(f"turn exceeded {limit} steps")
                before = len(collected)
                try:
                    update, nxt = await self._run_node(state, node)
                except GraphInterrupt as paused_turn:
                    paused = True
                    state["__interrupt__"] = [Interrupt(paused_turn.value)]
                    await self.store.save(thread_id, state)
                    for event in collected[before:]:
                        yield event
                    yield NodeUpdate(node, {})
                    return
                for event in collected[before:]:
                    yield event
                yield NodeUpdate(node, update)
                node = nxt
            state.pop("__interrupt__", None)
            await self.store.save(thread_id, state)
            usage = _usage_event()
            if usage is not None:
                yield usage
        finally:
            if not paused:
                self.chat.release_turn()
            reset_stream_writer(token)

    async def _run_node(self, state: dict, node: str) -> tuple[dict, str]:
        """Run one node and return ``(update, next)``.

        The update is applied before ``next`` is chosen, because the routing
        helpers read the state the node just wrote. A tool pause raises
        ``GraphInterrupt`` before that update is applied.
        """
        if node == "onboarding":
            update = await self.chat._onboarding(state) or {}
            _apply(state, update)
            return update, "end"
        if node == "assemble_context":
            update = await self.chat._assemble(state) or {}
            _apply(state, update)
            return update, self.chat._after_assemble(state)
        if node == "compact":
            update = await self.chat._compact(state) or {}
            _apply(state, update)
            return update, "agent"
        if node == "agent":
            update = await self.chat._agent(state) or {}
            _apply(state, update)
            return update, self.chat._after_agent(state)
        if node == "tools":
            update = await self.chat._tools(state) or {}
            _apply(state, update)
            return update, self.chat._after_tools(state)
        if node == "journal":
            update = await self.chat._journal(state) or {}
            _apply(state, update)
            return update, "capture"
        if node == "capture":
            update = await self.chat._capture(state) or {}
            _apply(state, update)
            return update, "end"
        raise RuntimeError(f"unknown turn node {node!r}")

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
