"""Role runner — the bounded subgraph that executes one declared role.

This is the shipped `ResearchSubagent` generalized: same shape (no checkpointer,
restricted read-only tools, a recursion cap, tool errors as JSON rather than
exceptions), parameterized by a `Role` instead of hardcoded.

Two things it adds on top of the pre-P5 worker:

**Provenance.** A run records which sources it actually consulted (every memory
hit it retrieved, path + chunk). Those become the report's `sources`. That gives
the anti-fabrication rule teeth: **a report produced without consulting anything
is unsourced**, so a specialist that answers from parametric knowledge instead of
the record is visibly flagged rather than quietly plausible.

**Cost.** Every run returns its `Spend` (wall clock, tool rounds, tokens), and
records a bounded scalar summary in the turn log — the multi-agent path is priced
per turn rather than discovered in the ledger later.

The graph is built per run. It is two nodes, so compiling it is negligible, and
it keeps concurrent fan-out (the orchestrator may run several researchers at
once) from sharing mutable state.

Note on the critic: its machine-readable `verdict`/`score` are set by the
orchestrator from the JEV sufficiency judgment, *not* parsed out of prose. Prose
parsing is a fragile interface; the judgment belongs in the judgment layer. The
critic's text rides back as the report's claim for the lead to read.
"""

from __future__ import annotations

import contextlib
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from langchain_core.messages import HumanMessage
from langgraph.graph import END, START, MessagesState, StateGraph

from iris import turnlog
from iris.agent.runtime import Runtime
from iris.agent.tools import Tool, memory_result_payload, run_memory_search
from iris.agents.handoff import Claim, Handoff, Source, Spend
from iris.agents.roles import Role

log = logging.getLogger("iris.agents.runner")


@dataclass
class _Run:
    """Per-run mutable state: what this run consulted. Never shared."""

    sources: list[Source] = field(default_factory=list)

    def remember(self, hits: Sequence) -> None:
        """Record consulted sources in retrieval order, without duplicates."""
        seen = {(s.path, s.chunk_index) for s in self.sources}
        for hit in hits:
            key = (hit.path, hit.chunk_index)
            if key in seen:
                continue
            seen.add(key)
            self.sources.append(Source(path=hit.path, chunk_index=hit.chunk_index))


class RoleRunner:
    """Runs one `Role` against one question. Never raises into the turn."""

    def __init__(self, runtime: Runtime, role: Role) -> None:
        self.runtime = runtime
        self.role = role

    # ── tools ────────────────────────────────────────────────────────────
    def _tools_for(self, run: _Run) -> list[Tool]:
        """The role's tools, with narrower implementations than the lead's.

        The allowlist in `Role.tools` names capabilities; here they are bound to
        the least-capable version that does the job — the researcher is fixed to
        the escalation lane (old daily notes, which is what it exists for) and
        the critic to the default lane (the current record, which is what it
        checks against).
        """
        role = self.role

        async def memory_search(query: str, top_k: int = 5) -> str:
            hits = await run_memory_search(self.runtime, query, top_k=top_k, lane=role.search_lane)
            run.remember(hits[:top_k])
            return json.dumps(
                {"ok": True, "results": [memory_result_payload(h) for h in hits[:top_k]]},
                ensure_ascii=False,
            )

        async def file_read(path: str) -> str:
            try:
                content = self.runtime.sandbox.read(path)
            except Exception as exc:  # noqa: BLE001 - tool errors surface as JSON
                return json.dumps({"ok": False, "error": str(exc)})
            return json.dumps({"ok": True, "content": content[:4000]})

        available = {
            "memory_search": Tool(
                "memory_search",
                {
                    "escalate": "Search long-term memory and daily notes (escalation lane).",
                    "default": "Search long-term memory and the current record.",
                }[role.search_lane],
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "minimum": 1, "maximum": 10},
                    },
                    "required": ["query"],
                },
                memory_search,
            ),
            "file_read": Tool(
                "file_read",
                "Read a text file from the sandbox (workspace/sandbox).",
                {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                file_read,
            ),
        }
        # The role's allowlist decides; a tool it does not declare is absent
        # from the schema the model is offered *and* from the runner's handler
        # table, so it cannot be called even if the model invents the name.
        return [available[name] for name in sorted(role.tools) if name in available]

    # ── graph ────────────────────────────────────────────────────────────
    def _build(self, run: _Run):
        role = self.role
        tools = self._tools_for(run)
        cap = role.max_tool_rounds

        class _State(MessagesState):
            rounds: int

        async def agent(state) -> dict:
            llm_messages: list[dict] = [{"role": "system", "content": role.system_prompt}]
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
                                    "function": {
                                        "name": tc["name"],
                                        "arguments": json.dumps(tc.get("args", {})),
                                    },
                                }
                                for tc in m.tool_calls
                            ],
                        }
                    )
                elif mtype == "tool":
                    llm_messages.append(
                        {"role": "tool", "content": m.content, "tool_call_id": m.tool_call_id}
                    )
                elif mtype in ("human", "ai"):
                    llm_messages.append({"role": mtype, "content": m.content})

            text, calls, _thinking = await self.runtime.llm.complete_with_tools(
                llm_messages,
                [t.schema() for t in tools],
                tier=role.tier,
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

        async def tools_node(state) -> dict:
            results = []
            for tc in state["messages"][-1].tool_calls:
                handler = next((t.handler for t in tools if t.name == tc["name"]), None)
                try:
                    out = await handler(**tc["args"]) if handler else '{"ok": false, "error": "unknown tool"}'
                except Exception as exc:  # noqa: BLE001 - tool errors must not kill the run
                    out = json.dumps({"ok": False, "error": str(exc)})
                results.append({"type": "tool", "content": out, "tool_call_id": tc["id"]})
            return {"messages": results}

        def after_agent(state):
            last = state["messages"][-1]
            if not getattr(last, "tool_calls", None):
                return "done"
            if state["rounds"] >= cap:
                return "done"
            return "tools"

        g = StateGraph(_State)
        g.add_node(role.name, agent)
        g.add_node("tools", tools_node)
        g.add_edge(START, role.name)
        g.add_conditional_edges(role.name, after_agent, {"tools": "tools", "done": END})
        g.add_edge("tools", role.name)
        return g.compile()

    # ── public API ───────────────────────────────────────────────────────
    async def run(self, question: str, *, session_id: str = "") -> Handoff:
        """Run the role on one question and return the handoff it produced.

        Never raises: a broken run comes back as an empty report (which renders
        as "no findings"), exactly as the pre-P5 worker returned "".
        """
        run = _Run()
        started = time.monotonic()
        # Token spend for *this* run. With concurrent fan-out the deltas of two
        # runs overlap, so a per-handoff count is approximate while the turn
        # total (`turnlog.usage_total()`) stays exact.
        tokens_before = turnlog.usage_total()
        rounds = 0
        report = ""
        try:
            graph = self._build(run)
            result = await graph.ainvoke(
                {"messages": [HumanMessage(content=question)], "rounds": 0},
                # Pre-P5 the researcher ran with a limit of 12 at 3 tool rounds
                # (4 super-steps each); the ratio is kept so the worker's
                # behaviour is unchanged.
                {"recursion_limit": 4 * self.role.max_tool_rounds},
            )
            rounds = int(result.get("rounds", 0))
            reports = [
                m.content
                for m in result["messages"]
                if getattr(m, "type", "") == "ai" and not getattr(m, "tool_calls", None)
            ]
            report = reports[-1] if reports else ""
        except Exception as exc:  # noqa: BLE001 - a role must never break a turn
            log.warning("%s run failed: %s", self.role.name, exc)

        truncate_d = False
        text = report or ""
        if len(text) > self.role.max_output_chars:
            text = text[: self.role.max_output_chars]
            truncate_d = True

        spend = Spend(
            tool_rounds=rounds,
            ms=int((time.monotonic() - started) * 1000),
            tokens=max(0, turnlog.usage_total() - tokens_before),
        )
        claims: tuple[Claim, ...] = ()
        if text.strip():
            claims = (Claim(text=text, sources=tuple(run.sources)),)

        handoff = Handoff(
            id=f"{self.role.name}-{int(time.monotonic() * 1000) % 100000}",
            from_role=self.role.name,
            to_role="lead",
            kind="report",
            question=question,
            claims=claims,
            spend=spend,
            truncated=truncate_d,
        )
        # Telemetry must never cost a reply. `turnlog.record` suppresses its own
        # failures, but a payload/key mismatch raises while *binding* the call,
        # before record runs — which is exactly how this line first broke the
        # shipped worker. Guard the call site too.
        with contextlib.suppress(Exception):
            turnlog.record("handoff", **handoff.trace_summary())
        return handoff

    async def report(self, question: str, *, session_id: str = "") -> str:
        """The run's text, for callers that only want the prose (`deep_dive`)."""
        handoff = await self.run(question, session_id=session_id)
        return handoff.claims[0].text if handoff.claims else ""
