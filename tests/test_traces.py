"""Unit tests: TraceLogger JSONL rotation + turn traces after a chat turn."""

from __future__ import annotations

from pathlib import Path

from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ChatGraph
from iris.agent.runtime import Runtime
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
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
        skills=SkillLibrary(files),
        sandbox=Sandbox(files.root / "sandbox"),
        traces=TraceLogger(files.root / "config" / "traces.jsonl"),
    )


async def test_chat_turn_records_trace(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    w = OnboardingWizard(files)
    for a in ["Omar", "warm", "short", "UTC", "4"]:
        w.apply_answer(a)
    graph = ChatGraph(make_runtime(files, FakeLLM()), MemorySaver())
    await graph.respond("hello there", session_id="t-trace")
    traces = TraceLogger(files.root / "config" / "traces.jsonl").recent()
    assert len(traces) == 1
    t = traces[0]
    assert t["session_id"] == "t-trace"
    assert t["user"] == "hello there"
    assert t["reply"] == "ok"
    assert t["tools"] == []
    assert t["latency_ms"] >= 0
    assert t["pending"] is None