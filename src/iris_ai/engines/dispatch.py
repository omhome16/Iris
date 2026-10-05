"""Which engine decisions ChatGraph should make.

The model call stays in ChatGraph. This module owns the difference between
react and plan-execute so those branches are not string compares at each site.
`PlanExecute.plan` is not the turn: that class does not call the model the
way the chat graph does.
"""

from __future__ import annotations

from typing import Any, Protocol


class TurnEngine(Protocol):
    name: str

    def short_circuit(self, state: dict) -> str | None: ...

    def wants_plan(self, state: dict) -> bool: ...

    def streams(self) -> bool: ...

    def tool_phase(self) -> dict: ...

    def finish(self, graph: Any, done: dict) -> None: ...

    def after_tools(self, state: dict) -> None: ...


class _React:
    name = "react"

    def short_circuit(self, state: dict) -> str | None:
        return None

    def wants_plan(self, state: dict) -> bool:
        return False

    def streams(self) -> bool:
        return True

    def tool_phase(self) -> dict:
        return {}

    def finish(self, graph: Any, done: dict) -> None:
        return None

    def after_tools(self, state: dict) -> None:
        return None


class _Plan:
    name = "plan-execute"

    def short_circuit(self, state: dict) -> str | None:
        if state.get("engine_phase") == "verify":
            return "verify"
        return None

    def wants_plan(self, state: dict) -> bool:
        return not state.get("engine_planned")

    def streams(self) -> bool:
        return False

    def tool_phase(self) -> dict:
        return {"engine_phase": "act"}

    def finish(self, graph: Any, done: dict) -> None:
        graph.engine_path.append("verify")
        done["engine_phase"] = "done"
        done["engine_event"] = "plan-execute: verify"

    def after_tools(self, state: dict) -> None:
        if state.get("engine_phase") == "act":
            state["engine_phase"] = "verify"


def resolve(name: str | None) -> TurnEngine:
    if name == "plan-execute":
        return _Plan()
    return _React()
