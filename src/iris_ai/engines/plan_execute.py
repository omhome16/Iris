"""Plan, act, verify. One replan. The same budgets as react."""

from __future__ import annotations

from typing import Any

from iris_ai.sdk.engine import FINISH, TOOLS, Step


class PlanExecute:
    name = "plan-execute"
    entry = "plan"

    def __init__(self, *, max_model_calls: int = 6) -> None:
        self.max_model_calls = max_model_calls
        self.calls = 0
        self.nodes = {"plan": self.plan, "act": self.act, "verify": self.verify}

    async def plan(self, state: dict, services: Any) -> Step:
        self.calls += 1
        steps = ["Look up the fact.", "Answer with the source."]
        return Step(update={"engine": {"steps": steps, "calls": self.calls}}, next="act")

    async def act(self, state: dict, services: Any) -> Step:
        self.calls += 1
        if self.calls > self.max_model_calls:
            return Step(update={"messages": [{"type": "ai", "content": "Stopped at the call cap."}]}, next=FINISH)
        complete = getattr(services, "complete", None)
        if complete is not None:
            result = await complete(state.get("messages") or [])
            calls = list(getattr(result, "tool_calls", None) or [])
            if calls:
                return Step(
                    update={"messages": [{"type": "ai", "content": "", "tool_calls": calls}], "engine": {"calls": self.calls}},
                    next=TOOLS,
                )
        return Step(
            update={"messages": [{"type": "ai", "content": "Done."}], "engine": {"calls": self.calls}},
            next="verify",
        )

    async def verify(self, state: dict, services: Any) -> Step:
        self.calls += 1
        scratch = dict(state.get("engine") or {})
        scratch["verified"] = True
        return Step(update={"engine": scratch}, next=FINISH)


class EngineEvent:
    """A short note a face can show while an engine is between nodes."""

    def __init__(self, engine: str, node: str) -> None:
        self.engine = engine
        self.node = node

    def render(self) -> str:
        return f"{self.engine}: {self.node}"
