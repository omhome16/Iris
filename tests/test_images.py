"""Image turns: a photo arrives as a base64 data URI, becomes OpenAI-style
content blocks, and reaches the LLM as-is (LiteLLM translates to Gemini
inline data). The write path logs the caption to the daily note."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ChatGraph, _to_llm_messages
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.onboarding import OnboardingWizard
from test_agent_graph import make_runtime

DATA_URI = "data:image/jpeg;base64,/9j/4AAQSkZJRg=="


class CaptureLLM(LLMClient):
    """Captures the last user message shape; replies without tools."""

    def __init__(self) -> None:
        self.last_user_content = None

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        for m in messages:
            if m["role"] == "user":
                self.last_user_content = m["content"]
        return "I see a photo.", [], ""

    async def complete(self, messages, **kwargs):
        return "{}"


async def _onboard(files: WorkspaceFiles) -> None:
    from fakes import WizardLLM

    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)


async def test_image_turn_reaches_llm_as_content_blocks(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    llm = CaptureLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    reply = await graph.respond("what is this?", session_id="img1", image=DATA_URI)
    assert reply == "I see a photo."

    blocks = llm.last_user_content
    assert isinstance(blocks, list)
    kinds = [b["type"] for b in blocks]
    assert kinds == ["text", "image_url"]
    assert blocks[1]["image_url"]["url"] == DATA_URI


async def test_plain_message_stays_a_string(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    await _onboard(files)
    llm = CaptureLLM()
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())

    await graph.respond("no image here", session_id="img2")
    assert isinstance(llm.last_user_content, str)
    assert llm.last_user_content == "no image here"


def test_to_llm_messages_preserves_content_blocks():
    from langchain_core.messages import HumanMessage

    blocks = [
        {"type": "text", "text": "look at this"},
        {"type": "image_url", "image_url": {"url": DATA_URI}},
    ]
    out = _to_llm_messages([HumanMessage(content=blocks)])
    assert out[0]["content"] == blocks
