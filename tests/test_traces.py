"""Unit tests: TraceLogger JSONL rotation + turn traces after a chat turn."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from fakes import skill_registry
from iris.agent.chat import ChatGraph
from iris.agent.runtime import Runtime
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.onboarding import OnboardingWizard
from iris.sandbox import Sandbox
from iris.trace import TraceLogger


def _logger(tmp_path: Path, max_bytes: int | None = None) -> TraceLogger:
    return TraceLogger(tmp_path / "traces.jsonl", max_bytes=max_bytes)


def test_record_appends_json_lines(tmp_path: Path):
    logger = _logger(tmp_path)
    logger.record({"ts": "a", "user": "hello", "tools": [], "latency_ms": 5})
    logger.record({"ts": "b", "user": "again", "tools": [], "latency_ms": 7})
    lines = logger.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert '{"ts": "a"' in lines[0]
    assert '{"ts": "b"' in lines[1]


def test_recent_returns_newest_first(tmp_path: Path):
    logger = _logger(tmp_path)
    for i in range(3):
        logger.record({"ts": f"t{i}", "latency_ms": i})
    got = logger.recent(2)
    assert [g["ts"] for g in got] == ["t2", "t1"]
    assert logger.recent(limit=0) == []


def test_rotation_keeps_one_generation(tmp_path: Path):
    logger = _logger(tmp_path, max_bytes=100)
    for i in range(50):
        logger.record({"ts": f"t{i:04d}", "padding": "x" * 20})
    assert logger.path.with_suffix(".jsonl.1").exists()
    assert logger.path.read_text(encoding="utf-8").strip() != ""


def test_recent_skips_corrupt_lines(tmp_path: Path):
    logger = _logger(tmp_path)
    logger.record({"ts": "good"})
    with logger.path.open("a", encoding="utf-8") as fh:
        fh.write("{corrupt json\n")
    got = logger.recent(10)
    assert len(got) == 1
    assert got[0]["ts"] == "good"


# ── end-to-end: a chat turn lands in traces.jsonl ───────────────────────────

class FakeLLM(LLMClient):
    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"chunks": 0, "origins": {}}


def make_runtime(files: WorkspaceFiles, llm: LLMClient) -> Runtime:
    return Runtime(
        files=files,
        llm=llm,
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(files.root / "sandbox"),
        traces=TraceLogger(files.root / "config" / "traces.jsonl"),
    )


async def test_chat_turn_records_trace(tmp_path: Path):
    from fakes import WizardLLM

    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    await graph.respond("hello there", session_id="t-trace")
    traces = TraceLogger(files.root / "config" / "traces.jsonl").recent()
    assert len(traces) == 1
    t = traces[0]
    assert t["session_id"] == "t-trace"
    # P6: metadata is the default trace content policy, so free text is replaced
    # by a length and a hash. The turn is still reconstructable (which session,
    # how long, which tools, what happened) without storing what was said.
    assert "user" not in t
    assert t["user_chars"] == len("hello there")
    assert t["user_hash"]
    assert "reply" not in t
    assert t["reply_chars"] == len("ok")
    assert t["tools"] == []
    assert t["latency_ms"] >= 0
    assert t["pending"] is None


async def test_the_trace_can_carry_content_when_the_owner_opts_in(tmp_path: Path, monkeypatch):
    """`trace_content = redacted` is the debugging mode: text is kept, secrets
    are not."""
    from fakes import WizardLLM
    from iris.config import settings

    monkeypatch.setattr(settings, "trace_content", "redacted")
    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files, WizardLLM())
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        await w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    await graph.respond("hello there", session_id="t-content")
    (t,) = TraceLogger(files.root / "config" / "traces.jsonl").recent()
    assert t["user"] == "hello there"
    assert t["reply"] == "ok"
