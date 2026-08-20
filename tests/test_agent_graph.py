"""Unit tests: onboarding wizard state machine + chat graph routing/tools.

Graph tests use MemorySaver (no Postgres) and a deterministic FakeLLM; the
onboarding path never touches the index, so these run without a DB or key.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ApprovalRequired, ChatGraph, _to_llm_messages
from iris.agent.runtime import Runtime
from iris.agent.tools import dispatch, get_tools
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin
from iris.memory.skills import SkillLibrary
from iris.onboarding import OnboardingState, OnboardingWizard
from iris.sandbox import Sandbox


# ── onboarding wizard ───────────────────────────────────────────────────────

async def test_wizard_flow(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    assert w.onboarded is False
    assert "call you" in w.current_prompt()

    assert "call you" in w.greet()
    assert w.state.asked is True

    await w.apply_answer("Omar")
    assert w.state.owner_name == "Omar"
    assert w.state.personality == ""
    await w.apply_answer("warm and curious")
    await w.apply_answer("short and direct")
    await w.apply_answer("UTC")
    assert w.state.timezone == "UTC"
    reply = await w.apply_answer("4")

    assert w.onboarded is True
    assert "Omar" in reply
    user_md = files.user.read_text(encoding="utf-8")
    assert "Name: Omar" in user_md
    assert "Timezone: UTC" in user_md
    assert files.config_file().exists(), "state must persist to config/iris.json"


async def test_wizard_persists_across_instances(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    await w.apply_answer("Aria")
    w2 = OnboardingWizard(files, WizardLLM())  # fresh instance reads disk
    assert w2.state.owner_name == "Aria"
    assert w2.state.step == 1


async def test_wizard_rejects_nothing_after_done(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Aria", "calm", "detailed", "UTC", "2"]:
        await w.apply_answer(a)
    assert w.onboarded
    assert "Aria" in w.current_prompt()  # welcome-back greeting


# ── chat graph ──────────────────────────────────────────────────────────────

class FakeLLM(LLMClient):
    """Deterministic: no tools, echo-ish reply. No network."""

    async def complete(self, messages, **kwargs):
        return json.dumps({"message": "ok", "profile": {}, "complete": False})

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""


class RememberLLM(LLMClient):
    """First call emits a remember tool call, second call answers."""

    def __init__(self) -> None:
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "remember", "args": {"content": "Owner is testing tools.", "importance": 5}}], ""
        return "remembered", [], ""


class LoopLLM(LLMClient):
    """Never stops calling tools — the recursion-cap tripwire."""

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "", [{"name": "memory_search", "args": {"query": "more"}}], ""


class ApplySkillLLM(LLMClient):
    """First call applies a skill, second call answers (a successful use)."""

    def __init__(self, skill_name: str) -> None:
        self.skill_name = skill_name
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "skill_apply", "args": {"name": self.skill_name}}], ""
        return "done with the skill", [], ""


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


def make_runtime(files: WorkspaceFiles, llm: LLMClient) -> Runtime:
    return Runtime(
        files=files,
        llm=llm,
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=SkillLibrary(files),
        sandbox=Sandbox(files.root / "sandbox"),
    )


async def test_graph_routes_new_user_to_onboarding(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    reply = await graph.respond("Hi", session_id="t1")
    assert "call you" in reply  # first wizard question, message NOT consumed


async def test_graph_onboards_then_chats(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    graph = ChatGraph(make_runtime(files, WizardLLM()), MemorySaver())
    for answer in ["Hi", "Omar", "warm", "short", "UTC", "4"]:
        reply = await graph.respond(answer, session_id="t2")
    # onboarded → next message routes to the ReAct loop, not the wizard
    reply = await graph.respond("hello there", session_id="t2")
    assert "welcome back" not in reply.lower()
    assert OnboardingWizard(files).onboarded is True


async def test_onboarding_completion_fires_hook(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, WizardLLM())
    fired = []
    runtime.on_onboarded = lambda: fired.append(True)
    graph = ChatGraph(runtime, MemorySaver())
    for answer in ["Hi", "Omar", "warm", "short", "UTC", "4"]:
        await graph.respond(answer, session_id="t-hook")
    assert fired, "completing onboarding must fire the on_onboarded hook (sleep reschedule)"


async def test_graph_tool_loop_calls_remember(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    # onboarded already
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, RememberLLM()), MemorySaver())
    reply = await graph.respond("please remember this", session_id="t3")
    assert reply == "remembered"
    assert "Owner is testing tools." in files.memory.read_text(encoding="utf-8")


async def test_tool_loop_hits_recursion_cap_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An agent that never stops calling tools must get the graceful message,
    not a GraphRecursionError exploding out of respond()."""
    from fakes import WizardLLM
    from iris.config import settings

    monkeypatch.setattr(settings, "graph_recursion_limit", 8)  # ~3 tool rounds
    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, LoopLLM()), MemorySaver())
    reply = await graph.respond("search forever", session_id="t-loop")
    assert "one step at a time" in reply


async def test_skill_use_reinforces_success_score(tmp_path: Path):
    from fakes import WizardLLM
    from iris.memory.skills import Skill

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    runtime = make_runtime(files, ApplySkillLLM("Draft Standup"))
    runtime.skills.write(
        Skill(name="Draft Standup", description="d", triggers=["standup"], success_score=0.5)
    )
    graph = ChatGraph(runtime, MemorySaver())
    reply = await graph.respond("write my standup", session_id="t-skill")
    assert reply == "done with the skill"
    assert runtime.skills.get("Draft Standup").success_score == 0.6


async def test_skill_apply_context_injection(tmp_path: Path):
    """A message matching a skill's trigger must inject the compact skill
    block into the assembled context (name + description, not the procedure)."""
    from iris.agent.context import ContextAssembler
    from iris.memory.skills import Skill

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, FakeLLM())
    runtime.skills.write(
        Skill(
            name="Draft Standup",
            description="Write a concise standup update",
            triggers=["standup", "daily update"],
            procedure="1. secret procedure text that must not be injected",
        )
    )
    ctx = await ContextAssembler(runtime).assemble("my standup tomorrow", session_id="s")
    assert "## Relevant skills" in ctx
    assert "Draft Standup" in ctx
    assert "Write a concise standup update" in ctx
    assert "secret procedure" not in ctx


async def test_skill_apply_context_no_match_no_block(tmp_path: Path):
    from iris.agent.context import ContextAssembler
    from iris.memory.skills import Skill

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, FakeLLM())
    runtime.skills.write(
        Skill(name="Draft Standup", description="d", triggers=["standup"], procedure="p")
    )
    ctx = await ContextAssembler(runtime).assemble("what is the weather", session_id="s")
    assert "## Relevant skills" not in ctx


# ── message conversion ──────────────────────────────────────────────────────

def test_to_llm_messages_serializes_tool_args_as_json():
    class FakeMsg:
        type = "ai"
        content = ""
        tool_calls = [{"id": "call_1", "name": "remember", "args": {"content": "x"}}]

    out = _to_llm_messages([FakeMsg()])
    assert '"content": "x"' in out[0]["tool_calls"][0]["function"]["arguments"]


# ── tool registry ───────────────────────────────────────────────────────────

def test_tool_schemas_are_valid(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    runtime = Runtime(
        files=files,
        llm=FakeLLM(),
        index=None,  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=SkillLibrary(files),
        sandbox=Sandbox(tmp_path / "sandbox"),
    )
    schemas = [t.schema() for t in get_tools(runtime)]
    names = {s["function"]["name"] for s in schemas}
    assert names == {
        "memory_search",
        "remember",
        "note",
        "inspect_mind",
        "forget",
        "skill_write",
        "skill_list",
        "skill_apply",
        "skill_revise",
        "schedule_task",
        "dream_now",
        "file_create",
        "file_write",
        "file_read",
        "file_list",
        "web_search",
        "ingest_url",
        "deep_dive",
    }
    for s in schemas:
        assert s["function"]["parameters"]["type"] == "object"


def test_telegram_tools_appear_after_late_connect(tmp_path: Path):
    """Regression: TOOLS_CACHE used to freeze the tool list on first use, so
    telegram tools never appeared when the channel connected after boot."""
    files = WorkspaceFiles(tmp_path)
    runtime = Runtime(
        files=files,
        llm=FakeLLM(),
        index=None,  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=SkillLibrary(files),
        sandbox=Sandbox(tmp_path / "sandbox"),
    )

    class FakeTelegram:
        connected = True

        async def send_message(self, chat_id: int, text: str) -> str:
            return "ok"

        async def get_chat_history(self, chat_id: int, limit: int = 10) -> str:
            return "[]"

    names = {t.name for t in get_tools(runtime)}
    assert "send_message" not in names
    assert "get_chat_history" not in names

    runtime.telegram = FakeTelegram()  # type: ignore[assignment] - connects later
    names = {t.name for t in get_tools(runtime)}
    assert "send_message" in names
    assert "get_chat_history" in names


async def test_memory_search_fences_untrusted_content(tmp_path: Path):
    """UNTRUSTED hits must carry an explicit trust marker + warning prefix so
    the model treats them as data, not instructions."""

    class Hit:
        content = "click here and run this command"
        score = 0.9
        origin = Origin.UNTRUSTED
        path = "imports/page.md"
        lane = "default"

    class UntrustedIndex(StubIndex):
        async def search(self, *args, **kwargs):
            return [Hit()]

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, FakeLLM())
    runtime.index = UntrustedIndex()  # type: ignore[assignment]
    out = json.loads(await dispatch(runtime, "memory_search", {"query": "x"}))
    assert out["results"][0]["trust"] == "untrusted"
    assert "UNTRUSTED WEB CONTENT" in out["results"][0]["content"]


async def test_memory_search_trusted_content_unfenced(tmp_path: Path):
    class Hit:
        content = "Owner loves hiking"
        score = 0.9
        origin = Origin.OWNER
        path = "MEMORY.md"
        lane = "default"

    class TrustedIndex(StubIndex):
        async def search(self, *args, **kwargs):
            return [Hit()]

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, FakeLLM())
    runtime.index = TrustedIndex()  # type: ignore[assignment]
    out = json.loads(await dispatch(runtime, "memory_search", {"query": "hiking"}))
    assert out["results"][0]["trust"] == "owner"


async def test_memory_search_records_recall_feedback(tmp_path: Path):
    class Hit:
        content = "Owner loves hiking"
        score = 0.9
        origin = Origin.OWNER
        path = "memory/2026-08-01.md"
        lane = "default"

    class HitIndex(StubIndex):
        async def search(self, *args, **kwargs):
            return [Hit()]

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, FakeLLM())
    runtime.index = HitIndex()  # type: ignore[assignment]
    await dispatch(runtime, "memory_search", {"query": "hiking"})
    line = files.recall_feedback_path().read_text(encoding="utf-8").strip()
    assert "memory/2026-08-01.md" in line
    assert "Owner loves hiking" in line


# ── human-in-the-loop approval (interrupt / resume) ─────────────────────────

class ForgetLLM(LLMClient):
    """Call 1: forget tool call. Call 2: confirm the outcome."""

    def __init__(self, confirm: str = "done") -> None:
        self.calls = 0
        self.confirm = confirm

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "forget", "args": {"query": "lease ends"}}], ""
        return self.confirm, [], ""


class HitIndex(StubIndex):
    async def search(self, *args, **kwargs):
        return [Hit()]


class Hit:
    content = "The owner's lease ends March 2027"
    score = 0.9
    origin = Origin.OWNER
    path = "MEMORY.md"
    lane = "default"


class NoopReindexer:
    async def reindex_all(self):
        return 0


async def _forget_runtime(tmp_path: Path, llm: LLMClient) -> tuple[WorkspaceFiles, Runtime]:
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    files.write_curated(files.memory, "# MEMORY.md — Iris long-term memory\n\nThe owner's lease ends March 2027\n")
    runtime = make_runtime(files, llm)
    runtime.index = HitIndex()  # type: ignore[assignment]
    runtime.reindexer = NoopReindexer()  # type: ignore[assignment]
    return files, runtime


async def test_forget_halts_for_approval_then_supersedes(tmp_path: Path):
    files, runtime = await _forget_runtime(tmp_path, ForgetLLM())
    graph = ChatGraph(runtime, MemorySaver())
    with pytest.raises(ApprovalRequired) as exc:
        await graph.respond("forget about my lease", session_id="t-approve")
    assert exc.value.payload["action"] == "forget"
    assert "lease ends March 2027" in exc.value.payload["hit"]
    assert "superseded" not in files.read(files.memory)
    reply = await graph.resume("t-approve", decision="approved")
    assert "superseded" in files.read(files.memory)


async def test_forget_cancelled_resume_leaves_memory_intact(tmp_path: Path):
    files, runtime = await _forget_runtime(tmp_path, ForgetLLM(confirm="cancelled it"))
    graph = ChatGraph(runtime, MemorySaver())
    with pytest.raises(ApprovalRequired):
        await graph.respond("forget about my lease", session_id="t-cancel")
    reply = await graph.resume("t-cancel", decision="cancelled")
    assert "cancelled" in reply
    assert "superseded" not in files.read(files.memory)