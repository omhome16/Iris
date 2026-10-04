"""Run an engine one node at a time. Validate every step."""

from __future__ import annotations

from typing import Any

from iris_ai.config import settings
from iris_ai.sdk.engine import FINISH, TOOLS, Step, check_step


class EngineRunner:
    def __init__(self, engine: Any, services: Any) -> None:
        self.engine = engine
        self.services = services
        self.path: list[str] = []
        self.rounds = 0

    async def run_node(self, name: str, state: dict) -> Step:
        if name not in self.engine.nodes:
            raise ValueError(f"unknown node {name!r}")
        self.rounds += 1
        limit = int(getattr(settings, "graph_recursion_limit", 25) or 25)
        if self.rounds > limit:
            raise RecursionError(f"engine exceeded {limit} steps")
        step = await self.engine.nodes[name](state, self.services)
        if not isinstance(step, Step):
            raise TypeError("engine node must return Step")
        issued = set(getattr(self.services.model, "issued", set()))
        check_step(step, nodes=set(self.engine.nodes), issued=issued)
        self.path.append(name if step.next == FINISH else f"{name}>{step.next}")
        if step.next == TOOLS and not step.after:
            raise ValueError("TOOLS requires after")
        return step
