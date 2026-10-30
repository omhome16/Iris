"""Reflexion. One critique, then one retry.

This is not plan-execute. There is no plan node and no verify node. The
engine asks for a draft, critiques that draft once, and asks for a revision.
`retries` is 1 after `respond` returns.
"""

from __future__ import annotations


class Reflexion:
    name = "reflexion"

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options
        self.retries = 0

    def short_circuit(self, state):
        del state
        return None

    def wants_plan(self, state) -> bool:
        del state
        return False

    def streams(self) -> bool:
        return False

    def tool_phase(self) -> dict:
        return {}

    def finish(self, graph, done) -> None:
        del graph
        done["engine_event"] = "reflexion: retry"

    def after_tools(self, state) -> None:
        state["engine_phase"] = "critique"

    async def respond(self, prompt: str, model) -> str:
        draft = await model.complete(prompt)
        critique = await model.complete(f"Critique the draft.\n{draft}")
        self.retries = 1
        revised = await model.complete(f"Revise once using this critique: {critique}\nTask: {prompt}")
        return revised
