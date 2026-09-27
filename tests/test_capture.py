"""Capture tests — the write-path safety net.

The v2 design's measured failure was volume, not correctness: zero `note` calls
in 36 traced turns. These tests pin the two properties that make the fix safe —
the prefilter keeps trivial turns free, and a capture is ADD-only evidence that
still has to pass the Light-phase gate before it can become curated memory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from fakes import WizardLLM
from iris_ai.agent.chat import ChatGraph
from iris_ai.config import settings
from iris_ai.memory.capture import (
    CaptureResult,
    condense,
    judge_capture,
    lexical_triggers,
    note_line,
    worth_capturing,
)
from iris_ai.memory.dreaming import _NOTE_LINE_RE
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.onboarding import OnboardingWizard
from iris_ai.trace import TraceLogger
from test_agent_graph import make_runtime

INFORMATIVE = (
    "I prefer my coffee black and I always book the 7am flight, "
    "my sister lives in Lisbon if that matters"
)


# ── deterministic prefilter ─────────────────────────────────────────────────

@pytest.mark.parametrize("text", ["ok", "thanks!", "haha", "yes", "good night", "hello?"])
def test_prefilter_rejects_trivia_so_trivial_turns_stay_free(text: str):
    assert worth_capturing(text, "Sure thing!") is False


def test_prefilter_rejects_short_questions_and_third_person():
    assert worth_capturing("what time is it?", "It's 4pm.") is False
    assert worth_capturing("the weather in Lisbon is lovely today", "Nice.") is False, "no first person"
    assert worth_capturing("I prefer tea?", "Noted.") is False, "too short"
    assert worth_capturing("I prefer loose leaf tea, always", "") is False, "needs a reply"


def test_prefilter_accepts_a_durable_first_person_fact():
    assert worth_capturing(INFORMATIVE, "Got it.") is True


def test_prefilter_respects_the_kill_switch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "capture_enabled", False)
    assert worth_capturing(INFORMATIVE, "Got it.") is False


def test_note_line_round_trips_through_the_light_phase_parser():
    """The line capture writes must be the line dreaming parses back out."""
    line = note_line(
        CaptureResult(captured=True, fact="Owner drinks black coffee", importance=7.0, triggers=("coffee",))
    )
    match = _NOTE_LINE_RE.match(line)
    assert match is not None
    assert match.group(1) == "7"
    assert match.group(2) == "Owner drinks black coffee"
    assert match.group(3) == "coffee"

    # No triggers is still a valid note line.
    plain = note_line(CaptureResult(captured=True, fact="Owner lives in Lisbon", importance=9.0))
    assert _NOTE_LINE_RE.match(plain).group(2) == "Owner lives in Lisbon"


def test_condense_and_triggers_are_deterministic_and_bounded():
    assert condense("  lots   of\nspace ") == "lots of space"
    long = condense("word " * 200, max_chars=50)
    assert len(long) <= 51 and long.endswith("…")
    triggers = lexical_triggers("I always book the cheapest direct flight to Lisbon")
    assert "lisbon" in triggers and "always" not in triggers and len(triggers) <= 4


# ── the judgment ────────────────────────────────────────────────────────────

class _JsonLLM:
    """Cheap-tier stand-in returning a scripted capture verdict."""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def complete(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload if isinstance(self.payload, str) else json.dumps(self.payload)


async def test_llm_judgment_captures_and_carries_importance():
    llm = _JsonLLM(
        {"capture": True, "fact": "Owner always books the 7am flight.", "importance": 8, "triggers": ["flight"]}
    )
    result = await judge_capture(llm, None, user_message=INFORMATIVE, ai_reply="Noted.")
    assert result.captured is True
    assert result.fact == "Owner always books the 7am flight."
    assert result.importance == 8.0
    assert result.triggers == ("flight",)
    assert llm.calls[0]["kwargs"]["tier"] == "cheap", "the capture judgment must not use the strong tier"


async def test_llm_judgment_declines_and_degrades_without_raising():
    assert (await judge_capture(_JsonLLM({"capture": False}), None, user_message=INFORMATIVE, ai_reply="k")).captured is False
    # Junk, an exception, and no model at all: no capture, and no raise.
    assert (await judge_capture(_JsonLLM("not json"), None, user_message=INFORMATIVE, ai_reply="k")).captured is False
    assert (
        await judge_capture(_JsonLLM(RuntimeError("provider down")), None, user_message=INFORMATIVE, ai_reply="k")
    ).captured is False
    assert (await judge_capture(None, None, user_message=INFORMATIVE, ai_reply="k")).captured is False
    # Below the importance floor, a model "yes" is still declined.
    low = _JsonLLM({"capture": True, "fact": "Owner once had a sandwich", "importance": 1})
    assert (await judge_capture(low, None, user_message=INFORMATIVE, ai_reply="k")).captured is False


async def test_jev_decides_and_skips_the_llm_entirely():
    from fakes import FakeJev

    jev = FakeJev(nouls={"durable": 0.91, "already_known": 0.05}, scores={"importance": 2.0})
    llm = _JsonLLM({"capture": True, "fact": "should never be used", "importance": 5})
    result = await judge_capture(llm, jev, user_message=INFORMATIVE, ai_reply="Noted.")

    assert result.captured is True
    assert result.importance == 7.0  # Score level 2 → 7 on the 1-10 scale
    assert result.fact == condense(INFORMATIVE), "Jev supplies the judgment; the owner's words supply the fact"
    assert llm.calls == [], "with JEV available the LLM judgment must not run"
    assert len(jev.calls) == 1, "one request carries the whole judgment"
    assert set(jev.calls[0]["questions"]) == {"durable", "already_known", "importance"}


async def test_jev_declines_known_or_trivial_turns():
    from fakes import FakeJev

    already = FakeJev(nouls={"durable": 0.9, "already_known": 0.9}, scores={"importance": 2.0})
    assert (await judge_capture(None, already, user_message=INFORMATIVE, ai_reply="k")).captured is False

    trivial = FakeJev(nouls={"durable": 0.2, "already_known": 0.0}, scores={"importance": 2.0})
    assert (await judge_capture(None, trivial, user_message=INFORMATIVE, ai_reply="k")).captured is False


async def test_jev_failure_falls_through_to_the_cheap_model():
    from fakes import FakeJev

    llm = _JsonLLM({"capture": True, "fact": "Owner flies at 7am.", "importance": 6})
    result = await judge_capture(llm, FakeJev(fail=True), user_message=INFORMATIVE, ai_reply="Noted.")
    assert result.captured is True and llm.calls, "a JEV outage must not disable capture"


# ── graph wiring ────────────────────────────────────────────────────────────

class _ReplyLLM(WizardLLM):
    """Chats normally; its `complete` serves the capture judgment."""

    def __init__(self, verdict) -> None:
        super().__init__()
        self.verdict = verdict
        self.judgments: list[str] = []

    async def complete(self, messages, **kwargs):
        if kwargs.get("json_mode") and "capture pass" in str(messages[0].get("content", "")):
            self.judgments.append(messages[1]["content"])
            return self.verdict if isinstance(self.verdict, str) else json.dumps(self.verdict)
        return await super().complete(messages, **kwargs)


async def _onboarded(tmp_path: Path) -> WorkspaceFiles:
    files = WorkspaceFiles(tmp_path)
    wizard = OnboardingWizard(files, WizardLLM())
    for answer in ["Omar", "warm", "short", "UTC", "4"]:
        await wizard.apply_answer(answer)
    return files


async def test_graph_captures_a_durable_fact_into_the_daily_note(tmp_path: Path):
    files = await _onboarded(tmp_path)
    llm = _ReplyLLM(
        {"capture": True, "fact": "Owner prefers black coffee.", "importance": 7, "triggers": ["coffee"]}
    )
    runtime = make_runtime(files, llm)
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    runtime.traces = TraceLogger(files.root / "config" / "traces.jsonl")
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond(INFORMATIVE, session_id="cap1")

    note = files.daily_note().read_text(encoding="utf-8")
    line = next(line for line in note.splitlines() if "(note)" in line)
    assert "Owner prefers black coffee." in line
    # The exact line must survive the Light phase's parser, or capture is a no-op.
    parsed = _NOTE_LINE_RE.match(line)
    assert parsed is not None and parsed.group(1) == "7" and parsed.group(3) == "coffee"
    assert llm.judgments, "the capture judgment must see the turn"

    # Recall-loop prevention: the judgment is shown what Iris already has.
    assert "Already in Iris's context" in llm.judgments[0]

    # The write path is observable in the turn trace, not taken on faith.
    traced = runtime.traces.recent()[0]
    assert traced["capture"].startswith("[7] Owner prefers black coffee.")

    # ...and the next turn must not report the previous turn's capture.
    await graph.respond("thanks!", session_id="cap1")
    assert runtime.traces.recent()[0]["capture"] == ""


async def test_graph_skips_trivial_turns_without_any_judgment(tmp_path: Path):
    files = await _onboarded(tmp_path)
    llm = _ReplyLLM({"capture": True, "fact": "x", "importance": 9})
    runtime = make_runtime(files, llm)
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond("thanks!", session_id="cap2")

    assert llm.judgments == [], "the prefilter must keep trivial turns free"
    written = files.daily_note().read_text(encoding="utf-8") if files.daily_note().exists() else ""
    assert "(note)" not in written


async def test_graph_never_captures_for_non_owner_origins(tmp_path: Path):
    files = await _onboarded(tmp_path)
    llm = _ReplyLLM({"capture": True, "fact": "Injected fact.", "importance": 9})
    runtime = make_runtime(files, llm)
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond(INFORMATIVE, session_id="cap3", origin="telegram")

    assert llm.judgments == []
    written = files.daily_note().read_text(encoding="utf-8") if files.daily_note().exists() else ""
    assert "(note)" not in written, "non-owner content must never reach the memory write path"


async def test_capture_is_capped_per_day(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "capture_max_per_day", 1)
    files = await _onboarded(tmp_path)
    llm = _ReplyLLM({"capture": True, "fact": "Owner prefers black coffee.", "importance": 7})
    runtime = make_runtime(files, llm)
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    graph = ChatGraph(runtime, MemorySaver())

    await graph.respond(INFORMATIVE, session_id="cap4")
    await graph.respond(INFORMATIVE + " and one more thing", session_id="cap4")

    assert files.daily_note().read_text(encoding="utf-8").count("(note)") == 1


async def test_capture_failure_never_breaks_the_turn(tmp_path: Path):
    files = await _onboarded(tmp_path)
    llm = _ReplyLLM("not json at all")
    runtime = make_runtime(files, llm)
    runtime.reindexer = _NoopReindexer()  # type: ignore[assignment]
    graph = ChatGraph(runtime, MemorySaver())

    reply = await graph.respond(INFORMATIVE, session_id="cap5")
    assert reply, "a failed capture judgment must not swallow the reply"


class _NoopReindexer:
    async def index_daily_note(self, rel: str = "") -> None:
        return None
