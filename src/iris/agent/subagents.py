"""Subagents — bounded, cheap-tier worker graphs.

Iris keeps its main ReAct loop on the strong model. For deep questions
(temporal / multi-hop / "dig through everything"), a ResearchSubagent takes
over: a small LangGraph subgraph with no checkpointer, running on the cheap
tier with a restricted toolset (memory_search via the escalation lane +
sandbox file_read), capped at 3 tool rounds and a 12-step recursion limit.
Its findings come back as a compact report the main agent uses as context —
without consuming strong-model tokens for the digging.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from iris.agent.runtime import Runtime
from iris.agent.tools import Tool, memory_result_payload, run_memory_search

log = logging.getLogger("iris.subagents")

_RESEARCH_SYSTEM = """You are Iris's research subagent. Dig through long-term
memory (memory_search, lane='escalate' for daily notes) and sandbox files
(file_read) to answer the owner's question. Be thorough but concise; your
report is injected into Iris's context. If memory has nothing, say so plainly.
Never fabricate facts. Finish with a short report."""

_MAX_TOOL_ROUNDS = 3


def _subagent_tools(runtime: Runtime) -> list[Tool]:
    """Restricted toolset for the researcher: escalate-lane memory search +
    sandbox reads only. Everything else (write, schedule, telegram) stays
    out of the subagent's hands."""

    async def memory_search(query: str, top_k: int = 5) -> str:
        # Same search-plus-recall-feedback path the main agent uses, on the
        # escalation lane: the subagent exists for temporal/multi-hop questions,
        # which is exactly what that lane is for.
        hits = await run_memory_search(runtime, query, top_k=top_k, lane="escalate")
        return json.dumps(
            {"ok": True, "results": [memory_result_payload(h) for h in hits[:top_k]]},
            ensure_ascii=False,
        )

    async def file_read(path: str) -> str:
        try:
            content = runtime.sandbox.read(path)
        except Exception as exc:  # noqa: BLE001 - tool errors surface as JSON
            return json.dumps({"ok": False, "error": str(exc)})
        return json.dumps({"ok": True, "content": content[:4000]})

    tools = [
        Tool("memory_search", "Search long-term memory and daily notes (escalation lane).", {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        }, memory_search),
        Tool("file_read", "Read a text file from the sandbox (workspace/sandbox).", {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }, file_read),
    ]
    return tools


class ResearchSubagent:
    """Compiled, checkpointer-less subgraph. Bounded by design: max tool
    rounds and a recursion cap so a runaway loop dies cheap."""

    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.tools = _subagent_tools(runtime)
        self.graph = self._build()

    class _State(MessagesState):
        rounds: int

    async def _agent(self, state) -> dict:
        llm_messages: list[dict] = [{"role": "system", "content": _RESEARCH_SYSTEM}]
        for m in state["messages"]:
            mtype = getattr(m, "type", "")
            if mtype == "ai" and getattr(m, "tool_calls", None):
                llm_messages.append(
                    {
                        "role": "assistant",
                        "content": m.content or "",
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))},
                            }
                            for tc in m.tool_calls
                        ],
                    }
                )
            elif mtype == "tool":
                llm_messages.append({"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id})
            elif mtype in ("human", "ai"):
                llm_messages.append({"role": mtype, "content": m.content})
        text, calls, _thinking = await self.runtime.llm.complete_with_tools(
            llm_messages,
            [t.schema() for t in self.tools],
            tier="cheap",
            max_attempts=2,
        )
        if calls:
            return {
                "messages": [
                    {
                        "type": "ai",
                        "content": text or "",
                        "tool_calls": [
                            {
                                # ids carry the round so they never collide
                                # across the subgraph's history
                                "id": f"sc_{state['rounds']}_{i}",
                                "name": c["name"],
                                "args": c["args"],
                                "type": "tool_call",
                            }
                            for i, c in enumerate(calls)
                        ],
                    }
                ],
                "rounds": state["rounds"] + 1,
            }
        return {"messages": [{"type": "ai", "content": text}]}

    async def _tools(self, state) -> dict:
        results = []
        for tc in state["messages"][-1].tool_calls:
            handler = next(
                (t.handler for t in self.tools if t.name == tc["name"]), None
            )
            try:
                out = await handler(**tc["args"]) if handler else '{"ok": false, "error": "unknown tool"}'
            except Exception as exc:  # noqa: BLE001 - tool errors must not kill the subgraph
                out = json.dumps({"ok": False, "error": str(exc)})
            results.append({"type": "tool", "content": out, "tool_call_id": tc["id"]})
        return {"messages": results}

    def _after_agent(self, state):
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return "done"
        if state["rounds"] >= _MAX_TOOL_ROUNDS:
            return "done"
        return "tools"

    def _build(self):
        g = StateGraph(self._State)
        g.add_node("researcher", self._agent)
        g.add_node("tools", self._tools)
        g.add_edge(START, "researcher")
        g.add_conditional_edges(
            "researcher", self._after_agent, {"tools": "tools", "done": END}
        )
        g.add_edge("tools", "researcher")
        return g.compile()

    async def research(self, query: str, *, session_id: str = "") -> str:
        """Run the researcher on a query; return its report text."""
        try:
            result = await self.graph.ainvoke(
                {
                    "messages": [HumanMessage(content=query)],
                    "rounds": 0,
                },
                {"recursion_limit": 12},
            )
        except Exception as exc:  # noqa: BLE001 - research must never break a turn
            log.warning("research subgraph failed: %s", exc)
            return ""
        reports = [
            m.content
            for m in result["messages"]
            if getattr(m, "type", "") == "ai" and not getattr(m, "tool_calls", None)
        ]
        return reports[-1] if reports else ""
