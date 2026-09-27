"""Reflection pass: retrieval-gated hallucination triage."""

from __future__ import annotations

import json
from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from fakes import FakeJev
from iris_ai import background, turnlog
from iris_ai.agent.chat import ChatGraph
from iris_ai.memory.llm import LLMClient
from iris_ai.memory.reflection import ReflectionPass, _claims, retrieved_excerpts
from test_agent_graph import make_runtime


class RetrieveThenReflectLLM(LLMClient):
    """Turn 1: memory_search tool call; turn 2: a confident (unverified) claim.

    complete() answers the write-path extraction prompt with empty memories
    and the reflection prompt with a canned verdict."""

    def __init__(self, flagged: list[dict]) -> None:
        self.calls = 0
        self.flagged = flagged

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return "", [{"name": "memory_search", "args": {"query": "lease"}}], ""
        return "Your lease renews on September 1st 2026.", [], ""

    async def complete(self, messages, **kwargs):
        user = messages[-1]["content"] if messages else ""
        if "Retrieved memory excerpts" in user:
            return json.dumps({"flagged": self.flagged})
        return json.dumps({"memories": []})


class ExplodingReflectLLM(RetrieveThenReflectLLM):
    async def complete(self, messages, **kwargs):
        raise RuntimeError("provider down")


def test_retrieved_excerpts_collects_tool_messages_only():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    state = {
        "messages": [
            HumanMessage(content="hi"),
            AIMessage(content="", tool_calls=[{"name": "memory_search", "id": "c1", "args": {"query": "x"}}]),
            ToolMessage(content="hit 1", tool_call_id="c1"),
            AIMessage(content="answer", tool_calls=[]),
        ]
    }
    assert retrieved_excerpts(state) == ["hit 1"]


async def test_reflection_writes_flags_when_retrieval_happened(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([{"claim": "lease ends Sept 1", "why": "no excerpt supports the date"}])
    pass_ = ReflectionPass(llm, path)
    await pass_.check(
        user_message="when does my lease end?",
        ai_reply="Your lease renews on September 1st 2026.",
        retrieved=["the owner pays rent on the 1st"],
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert "lease ends Sept 1" in data["claim"]
    assert data["why"]


async def test_reflection_skipped_without_retrieval(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([{"claim": "x", "why": "y"}])
    pass_ = ReflectionPass(llm, path)
    await pass_.check(user_message="hi", ai_reply="hello", retrieved=[])
    assert not path.exists()


async def test_reflection_llm_failure_never_raises(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    pass_ = ReflectionPass(ExplodingReflectLLM([{"claim": "x", "why": "y"}]), path)
    await pass_.check(
        user_message="q", ai_reply="a", retrieved=["some excerpt"]
    )  # must not raise
    assert not path.exists()


async def test_reflection_no_flags_no_file(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([])
    pass_ = ReflectionPass(llm, path)
    await pass_.check(user_message="q", ai_reply="a", retrieved=["e"])
    assert not path.exists()


async def test_graph_turn_with_retrieval_writes_flag(tmp_path: Path):
    from fakes import WizardLLM
    from iris_ai.memory.files import WorkspaceFiles
    from iris_ai.onboarding import OnboardingWizard

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    llm = RetrieveThenReflectLLM([{"claim": "lease ends Sept 1", "why": "unsupported"}])
    graph = ChatGraph(make_runtime(files, llm), MemorySaver())
    reply = await graph.respond("when does my lease end?", session_id="t-flag")
    assert "September 1st" in reply

    # The reflection pass runs off the reply path by default, so waiting for it
    # is explicit. Asserting before the drain would be a race: it usually
    # finished, because the graph awaits more work after spawning it.
    assert await background.drain() == 0
    flags = files.root / "config" / "hallucination_flags.jsonl"
    assert flags.exists()
    assert "lease ends Sept 1" in flags.read_text(encoding="utf-8")


# ── the JEV path (P8): a decision, not a completion ──────────────────────


async def test_a_claim_sentence_carries_a_support_probability(tmp_path: Path):
    """One batched judgment replaces the fact-checking completion: every
    sentence gets a probability, and a low one becomes a flag."""
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    jev = FakeJev(nouls={"c0": 0.02, "c1": 0.94})
    pass_ = ReflectionPass(RetrieveThenReflectLLM([]), path, jev=jev)

    await pass_.check(
        user_message="when does my lease end and what do I pay?",
        ai_reply=(
            "Your lease renews on September 1st 2026. "
            "You pay rent on the first of each month."
        ),
        retrieved=["the owner pays rent on the 1st"],
    )

    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1  # only the unsupported sentence
    data = json.loads(lines[0])
    assert "September 1st 2026" in data["claim"]
    assert "p=0.02" in data["why"]
    assert len(jev.calls) == 1  # one request for the whole reply
    assert set(jev.question_ids()) == {"c0", "c1"}


async def test_every_claim_probability_is_recorded_in_the_trace(tmp_path: Path):
    """A judgment nobody can inspect is indistinguishable from one that silently
    failed — and this pass can flag *nothing* and still have run."""
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    jev = FakeJev(nouls={"c0": 0.91, "c1": 0.88})
    pass_ = ReflectionPass(RetrieveThenReflectLLM([]), path, jev=jev)

    with turnlog.collect() as log:
        await pass_.check(
            user_message="q",
            ai_reply="Your lease renews on September 1st 2026. You pay rent on the first.",
            retrieved=["excerpt"],
        )

    assert not path.exists()  # nothing flagged
    events = [e for e in log.judgments if e["kind"] == "reflection_claims"]
    assert len(events) == 1
    assert [c["support"] for c in events[0]["probabilities"]] == [0.91, 0.88]
    mode = next(e for e in log.judgments if e["kind"] == "reflection_decision")
    assert mode["mode"] == "jev"
    assert mode["claims"] == 2 and mode["flagged"] == 0


async def test_a_jev_failure_falls_back_to_the_model(tmp_path: Path):
    """JEV is an accelerator, not a dependency: a broken judgment layer must
    leave the pass working exactly as it did before."""
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    jev = FakeJev(errors=True)  # enabled, but every request fails
    llm = RetrieveThenReflectLLM([{"claim": "lease ends Sept 1", "why": "unsupported"}])
    pass_ = ReflectionPass(llm, path, jev=jev)

    await pass_.check(
        user_message="q", ai_reply="Your lease renews on September 1st 2026.", retrieved=["e"]
    )

    assert "lease ends Sept 1" in path.read_text(encoding="utf-8")
    assert jev.calls  # it was tried first


async def test_jev_absent_still_uses_the_model(tmp_path: Path):
    path = tmp_path / "config" / "hallucination_flags.jsonl"
    llm = RetrieveThenReflectLLM([{"claim": "lease ends Sept 1", "why": "y"}])
    pass_ = ReflectionPass(llm, path, jev=None)
    assert not pass_.jev_enabled
    await pass_.check(user_message="q", ai_reply="Your lease renews on September 1st 2026.", retrieved=["e"])
    assert "lease ends Sept 1" in path.read_text(encoding="utf-8")


def test_claims_are_sentences_and_fragments_are_dropped():
    """The claim list has to be deterministic — probabilities are recorded
    against positions, so the same reply must always split the same way."""
    reply = "Yes. Your lease renews on September 1st 2026. You pay rent on the first of each month."
    claims = _claims(reply)
    assert claims == [
        "Your lease renews on September 1st 2026.",
        "You pay rent on the first of each month.",
    ]
    assert _claims("Sure!") == []
    # Bounded: a long reply cannot grow the JEV state without limit.
    assert len(_claims("A sentence that is long enough to count. " * 40)) <= 12


async def test_the_graph_hands_the_jev_client_to_the_reflection_pass(tmp_path: Path):
    """The wiring, not just the unit: a real turn with a JEV client attached
    must flag through JEV and never reach the model path."""
    from fakes import WizardLLM
    from iris_ai.memory.files import WorkspaceFiles
    from iris_ai.onboarding import OnboardingWizard

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)

    llm = RetrieveThenReflectLLM([{"claim": "SHOULD NOT BE USED", "why": "model path"}])
    runtime = make_runtime(files, llm)
    jev = FakeJev(default_noul=0.01)  # nothing is supported, per JEV
    runtime.jev = jev
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond("when does my lease end?", session_id="t-jev")
    assert await background.drain() == 0

    flags = files.root / "config" / "hallucination_flags.jsonl"
    body = flags.read_text(encoding="utf-8")
    assert "JEV support p=" in body
    assert "SHOULD NOT BE USED" not in body
    assert jev.calls


async def test_graph_turn_without_retrieval_no_flag(tmp_path: Path):
    from fakes import WizardLLM
    from iris_ai.memory.files import WorkspaceFiles
    from iris_ai.onboarding import OnboardingWizard
    from test_agent_graph import FakeLLM

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    await graph.respond("hi", session_id="t-noflag")
    assert not (files.root / "config" / "hallucination_flags.jsonl").exists()
