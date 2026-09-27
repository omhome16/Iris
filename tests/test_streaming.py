"""Streaming chat: thinking / text / tool-call events surface through
respond_stream, and complete_with_tools returns the thinking text."""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from iris_ai.agent.chat import ChatGraph
from iris_ai.memory.llm import LLMClient
from test_agent_graph import make_runtime


class StreamLLM(LLMClient):
    """Streams thinking, then text, then a tool call, then final text."""

    def __init__(self) -> None:
        self.calls = 0

    async def stream_complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            yield ("thinking", "Let me check")
            yield ("thinking", " memory.")
            yield ("text", "I found")
            yield ("text", " it:")
            yield ("tool_call", {"id": "c1", "name": "memory_search", "args": {"query": "x"}})
            yield ("done", "Let me check memory.")
            return
        yield ("text", "Here's the")
        yield ("text", " answer.")
        yield ("done", "")

    async def complete(self, messages, **kwargs):
        return "{}"

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], "thinking text"


async def _onboard(files) -> None:
    from fakes import WizardLLM
    from iris_ai.onboarding import OnboardingWizard

    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)


async def test_respond_stream_emits_thinking_text_tool_call(tmp_path: Path):
    from iris_ai.memory.files import WorkspaceFiles

    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    llm = StreamLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    events = [e async for e in graph.respond_stream("what do you remember?", session_id="s1")]

    kinds = [k for k, _ in events]
    assert "custom" in kinds and "updates" in kinds

    thinking = "".join(p["delta"] for k, p in events if k == "custom" and p["kind"] == "thinking")
    assert thinking == "Let me check memory."

    tool_events = [p for k, p in events if k == "custom" and p["kind"] == "tool_call"]
    assert tool_events and tool_events[0]["call"]["name"] == "memory_search"

    text = "".join(p["delta"] for k, p in events if k == "custom" and p["kind"] == "text")
    assert text.startswith("I found it:")
    assert text.endswith("Here's the answer.")


async def test_respond_stream_final_reply_in_updates(tmp_path: Path):
    from iris_ai.memory.files import WorkspaceFiles

    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    llm = StreamLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    final = ""
    async for kind, data in graph.respond_stream("hello again", session_id="s2"):
        if kind == "updates":
            for _node, update in (data or {}).items():
                for m in (update or {}).get("messages", []):
                    mtype = m.get("type") if isinstance(m, dict) else getattr(m, "type", "")
                    mcalls = m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)
                    mcontent = m.get("content") if isinstance(m, dict) else m.content
                    if mtype == "ai" and not mcalls:
                        final = mcontent
    assert final == "Here's the answer."


async def test_complete_with_tools_returns_thinking(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Non-streamed path surfaces reasoning_content as the third tuple item."""
    import iris_ai.memory.llm as llm_mod

    class Resp:
        def __init__(self):
            self.choices = [
                type(
                    "C",
                    (),
                    {
                        "message": type(
                            "M",
                            (),
                            {
                                "content": "answer",
                                "tool_calls": None,
                                "reasoning_content": "I reasoned here",
                            },
                        )()
                    },
                )()
            ]
            self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()

    async def fake_acompletion(**kwargs):
        return Resp()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    client = LLMClient(ledger=None)
    text, calls, thinking = await client.complete_with_tools([{"role": "user", "content": "hi"}])
    assert text == "answer"
    assert calls == []
    assert thinking == "I reasoned here"
