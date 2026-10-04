"""The ReAct engine: one node, `agent`, which is today's model call.

Tools are not run here. The step asks the kernel to run them, then come back.
"""

from __future__ import annotations

from typing import Any

from iris_ai.sdk.engine import FINISH, TOOLS, Step


class ReactEngine:
    name = "react"
    entry = "agent"

    def __init__(self, agent) -> None:
        self._agent = agent
        self.nodes = {"agent": self.agent}

    async def agent(self, state: dict, services: Any) -> Step:
        update = await self._agent(state)
        messages = update.get("messages") or []
        last = messages[-1] if messages else {}
        calls = last.get("tool_calls") if isinstance(last, dict) else None
        if calls:
            return Step(update=update, next=TOOLS, after="agent")
        return Step(update=update, next=FINISH)
