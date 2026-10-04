"""Kernel services an engine may call. Engines do not dispatch tools."""

from __future__ import annotations

import uuid
from typing import Any

from iris_ai.config import settings
from iris_ai.prompt import CONTRACT


class PromptService:
    """The system message layout the agent node has always used."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def system(self, state: dict) -> str:
        from iris_ai.agent.tools import tool_surface
        from iris_ai.prompt import render_system

        system = (
            render_system(
                self.runtime.files.root,
                today=self.runtime.files.today().isoformat(),
                timezone=settings.iris_timezone,
                persona=getattr(self.runtime, "persona_choice", "") or "",
            )
            + f"\n\nContext:\n{state.get('memory_context', '')}"
        )
        if state.get("tool_failures"):
            system += (
                "\n\nTool results from this turn failed: "
                f"{state['tool_failures']}. Do not tell the owner those actions succeeded. "
                "Say what failed and what you can do instead."
            )
        if state.get("conversation_summary"):
            system += f"\n\n## Summary of earlier conversation\n{state['conversation_summary']}"
        catalog = tool_surface(
            self.runtime,
            state.get("origin") or "owner",
            state.get("active_skills") or (),
            state.get("loaded_tools") or (),
        )[1]
        if catalog:
            system += f"\n\n{catalog}"
        return system

    def messages(self, state: dict, history: list[dict]) -> list[dict]:
        return [{"role": "system", "content": self.system(state)}, *history]


class ToolSurface:
    """Schemas only. Dispatch stays in the kernel tools node."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def schemas(self, state: dict) -> list[dict]:
        from iris_ai.agent.tools import tool_schemas

        return tool_schemas(
            self.runtime,
            state.get("origin") or "owner",
            state.get("active_skills") or (),
            state.get("loaded_tools") or (),
        )


class ModelService:
    """One model call. Visible calls are the ones that stream to the owner.

    Tool-call ids are minted here. An engine that invents an id is recorded
    as engine-originated by the runner, which rejects ids this service did not mint
    unless the engine asked `mint_id` first.
    """

    def __init__(self, llm: Any) -> None:
        self.llm = llm
        self.issued: set[str] = set()

    def mint_id(self) -> str:
        call_id = f"call_{uuid.uuid4().hex}"
        self.issued.add(call_id)
        return call_id

    def _with_contract(self, messages: list[dict], *, tools: list | None, visible: bool) -> list[dict]:
        if not messages:
            return messages
        first = messages[0]
        content = str(first.get("content") or "")
        needs = bool(tools) or visible
        missing = CONTRACT.split("{", 1)[0][:40] not in content and "Operating contract" not in content
        bare = "You are Iris" not in content and not content.startswith("## ")
        if needs and missing and bare:
            return [{"role": "system", "content": "Follow the harness contract.\n\n" + content}, *messages[1:]]
        return messages

    async def complete(
        self,
        messages: list[dict],
        *,
        tools: list | None = None,
        tier: str = "strong",
        visible: bool = True,
    ) -> tuple[str, list[dict]]:
        prepared = self._with_contract(messages, tools=tools, visible=visible)
        if tools is not None:
            text, calls, _thinking = await self.llm.complete_with_tools(prepared, tools, max_attempts=2)
        else:
            text = await self.llm.complete(prepared[-1]["content"] if prepared else "", tier=tier)
            calls = []
        minted = []
        for call in calls or []:
            call_id = call.get("id") or self.mint_id()
            self.issued.add(call_id)
            minted.append({**call, "id": call_id})
        return str(text or ""), minted
