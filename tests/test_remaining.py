"""Approval binding, artifact cleanup, audit-platform refusal, and candidate eval."""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

from iris_ai.approval import ApprovalGate, Envelope
from iris_ai.artifacts.store import gc, ingest
from iris_ai.components.lock import pin
from iris_ai.config import settings
from iris_ai.isolation.linux_jail import apply
from iris_ai.isolation.policy import execution_refusal
from iris_ai.lifecycle.journal import recover
from iris_ai.sdk.context import Clock
from iris_ai.timeutil import now, today


def _harness(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_config", str(harness))


def test_an_expired_approval_is_refused():
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}, expires="2000-01-01T00:00:00+00:00").payload()
    verdict = ApprovalGate().verify(pending=pending, decision="approved", thread="t")
    assert verdict.refused
    assert "expired" in verdict.reason


def test_an_activation_card_is_refused_after_the_generation_moves(tmp_path: Path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    from iris_ai.agent.tools import component_tools
    from iris_ai.kernel.pause import GraphInterrupt

    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / ".staging" / "persona" / "voice"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "persona"\nname = "voice"\napi_version = "iris/v1"\nentry = "component:Voice"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Voice:\n    def __init__(self, ctx, **options):\n        self.ctx = ctx\n    def text(self):\n        return 'x'\n",
        encoding="utf-8",
    )
    runtime = SimpleNamespace(generation_id="gen-1")
    tools = {item.name: item for item in component_tools(runtime)}
    try:
        asyncio.run(tools["component_activate"].handler("persona", "voice"))
    except GraphInterrupt as paused:
        pending = paused.value
    else:
        raise AssertionError("activation must pause for approval")
    assert pending["generation"] == "gen-1"
    assert pending["expires"]
    moved = ApprovalGate().verify(
        pending=pending, decision="approved", thread="t", current_generation="gen-2"
    )
    assert moved.refused
    assert "generation" in moved.reason
    expired = dict(pending)
    expired["expires"] = "2000-01-01T00:00:00+00:00"
    stale = ApprovalGate().verify(pending=expired, decision="approved", thread="t", current_generation="gen-1")
    assert stale.refused
    assert "expired" in stale.reason
    try:
        asyncio.run(tools["component_rollback"].handler("persona"))
    except GraphInterrupt as paused:
        rollback = paused.value
    else:
        raise AssertionError("rollback must pause for approval")
    assert rollback["generation"] == "gen-1"


def test_a_generation_change_refuses_the_old_approval():
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}, generation="aaaa").payload()
    verdict = ApprovalGate().verify(
        pending=pending, decision="approved", thread="t", current_generation="bbbb"
    )
    assert verdict.refused
    assert "generation" in verdict.reason


def test_a_tampered_artifact_refuses_resume(tmp_path: Path, monkeypatch):
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "context" / "voice"
    folder.mkdir(parents=True)
    (folder / "component.py").write_text("class Component:\n    pass\n", encoding="utf-8")
    digest = ingest(folder)
    pending = Envelope(action="component_activate", call_id="c1", args={"digest": digest}, artifact=digest).payload()
    gate = ApprovalGate()
    assert gate.verify(pending=pending, decision="denied", thread="t").allowed
    stored = tmp_path / "components" / ".store" / f"sha256-{digest}" / "component.py"
    stored.write_text("class Component:\n    pass\n# tampered\n", encoding="utf-8")
    refused = gate.verify(pending=pending, decision="approved", thread="t")
    assert refused.refused
    assert "artifact" in refused.reason


def test_gc_drops_a_store_the_lock_does_not_name(tmp_path: Path, monkeypatch):
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "context" / "voice"
    folder.mkdir(parents=True)
    (folder / "component.py").write_text("x = 1\n", encoding="utf-8")
    digest = ingest(folder)
    assert gc() == [digest]
    digest = ingest(folder)
    pin("context", "voice", source="local", digest=digest)
    assert gc() == []


def test_recover_names_a_pin_whose_store_is_gone(tmp_path: Path, monkeypatch):
    _harness(tmp_path, monkeypatch)
    pin("context", "voice", source="local", digest="a" * 64)
    problems = recover()
    assert problems
    assert "voice" in problems[0]
    from iris_ai.cli.doctor import run_checks

    named = [item for item in run_checks(tmp_path) if item.name == "component pin"]
    assert named
    assert named[0].level == "fail"
    assert "voice" in named[0].detail


def test_the_component_clock_uses_the_harness_day():
    assert Clock().today() == today()
    assert Clock().now() <= now() + timedelta(seconds=2)


def test_doctor_names_a_windows_component_the_kernel_will_not_load(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(settings, "allow_audit_isolation", False)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "harness.toml").write_text(
        '[components]\npersona = "voice"\n',
        encoding="utf-8",
    )
    folder = tmp_path / "components" / "persona" / "voice"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "persona"\nname = "voice"\napi_version = "iris/v1"\nentry = "component:Voice"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Voice:\n    def text(self):\n        return 'x'\n",
        encoding="utf-8",
    )
    from iris_ai.cli.doctor import _component_checks

    checks = _component_checks(tmp_path)
    failed = [item for item in checks if item.name == "component persona" and item.level == "fail"]
    assert failed
    assert "ALLOW_AUDIT_ISOLATION" in failed[0].detail
    assert not any(item.name == "component persona" and item.level == "ok" for item in checks)


def test_windows_refuses_an_executable_component_until_the_owner_allows_it(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(settings, "allow_audit_isolation", False)
    assert "ALLOW_AUDIT_ISOLATION" in execution_refusal("context")
    monkeypatch.setattr(settings, "allow_audit_isolation", True)
    assert execution_refusal("context") == ""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(settings, "allow_audit_isolation", False)
    assert execution_refusal("context") == ""


def test_linux_jail_reports_audit_when_landlock_is_absent(tmp_path: Path):
    if sys.platform.startswith("linux"):
        return
    assert apply(tmp_path, [tmp_path]) == "audit"


def test_eval_runs_a_local_capture_candidate(tmp_path: Path, monkeypatch):
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "capture" / "keepish"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "capture"\nname = "keepish"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Component:\n"
        "    async def maybe_capture(self, *, user_message, reply):\n"
        "        return user_message if 'keep' in user_message else None\n",
        encoding="utf-8",
    )
    from iris_ai.eval.score import score_capture

    kept = score_capture("keepish", {"input": "please keep this", "expect": {"store": True}})
    dropped = score_capture("keepish", {"input": "hello", "expect": {"store": False}})
    assert kept["correct"] == 1.0
    assert dropped["correct"] == 1.0


def test_eval_scores_a_v1_extract_and_propose(tmp_path: Path, monkeypatch):
    _harness(tmp_path, monkeypatch)
    capture = tmp_path / "components" / "capture" / "keeper"
    capture.mkdir(parents=True)
    (capture / "component.toml").write_text(
        'kind = "capture"\nname = "keeper"\napi_version = "iris/v1"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (capture / "component.py").write_text(
        "class Component:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "    async def extract(self, request):\n"
        "        from iris_ai.sdk.types import MemoryCandidate\n"
        "        if 'keep' in request.user_message:\n"
        "            return [MemoryCandidate(content=request.user_message)]\n"
        "        return []\n",
        encoding="utf-8",
    )
    consolidator = tmp_path / "components" / "consolidator" / "flagger"
    consolidator.mkdir(parents=True)
    (consolidator / "component.toml").write_text(
        'kind = "consolidator"\nname = "flagger"\napi_version = "iris/v1"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (consolidator / "component.py").write_text(
        "class Component:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "    async def propose(self, request):\n"
        "        from datetime import date\n"
        "        from iris_ai.sdk.types import Conflict, ConsolidationPlan, MemoryCandidate, MemoryItem\n"
        "        if 'conflict' not in request.notes:\n"
        "            return ConsolidationPlan()\n"
        "        item = MemoryItem(\n"
        "            id='1', content=request.curated, path='MEMORY.md',\n"
        "            observed_at=date.today(), origin='owner',\n"
        "        )\n"
        "        incoming = MemoryCandidate(content=request.notes)\n"
        "        return ConsolidationPlan(conflicts=(Conflict(existing=item, incoming=incoming, reason='clash'),))\n",
        encoding="utf-8",
    )
    from iris_ai.eval.score import score_capture, score_consolidator

    dropped = score_capture("keeper", {"input": "hello", "expect": {"store": False}})
    kept = score_capture("keeper", {"input": "please keep this", "expect": {"store": True}})
    assert dropped["correct"] == 1.0
    assert kept["stored_per_turn"] == 1.0
    clash = score_consolidator(
        "flagger",
        {"curated": "the sky is blue", "input": "conflict: the sky is green", "expect": {"conflict": True}},
    )
    clear = score_consolidator(
        "flagger",
        {"curated": "the sky is blue", "input": "the sky is blue", "expect": {"conflict": False}},
    )
    assert clash["silent_overwrite"] == 0.0
    assert clear["false_supersede"] == 0.0


def test_repl_help_commands_are_in_the_shared_registry():
    from iris_ai.cli.chat import REPL_HELP
    from iris_ai.commands import names

    for command in ("/exit", "/quit", "/help", "/explain", "/trace", "/conflicts", "/reload"):
        assert command in REPL_HELP
        if command != "/reload":
            assert command in names()


def test_an_unknown_model_is_marked_unpriced(tmp_path: Path):
    from iris_ai.ledger import CostLedger

    ledger = CostLedger(tmp_path / "calls.jsonl")
    ledger.record(model="not-a-priced-model", tier="cheap", prompt_tokens=10, completion_tokens=2)
    row = ledger.rows()[0]
    assert row["unpriced"] is True
    assert row["cost"] == 0.0


def test_a_successful_engine_step_clears_the_failure_streak():
    from types import SimpleNamespace

    from iris_ai.agent.chat import ChatGraph, _engine_failed

    graph = ChatGraph.__new__(ChatGraph)
    graph.runtime = SimpleNamespace(engine_fails=0, engine_name="plan-execute")
    graph.engine_name = "plan-execute"
    _engine_failed(graph, RuntimeError("plan"))
    _engine_failed(graph, RuntimeError("plan"))
    graph.runtime.engine_fails = 0
    _engine_failed(graph, RuntimeError("plan"))
    assert graph.runtime.engine_name == "plan-execute"
    assert graph.runtime.engine_fails == 1


def test_sqlite_threads_use_wal_and_keep_the_latest_save(tmp_path: Path):
    import asyncio

    from iris_ai.kernel.threads import SqliteThreadStore

    store = SqliteThreadStore(str(tmp_path / "threads.db"))
    mode = store._conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert str(mode).lower() == "wal"
    asyncio.run(store.save("t", {"messages": [{"type": "human", "content": "one"}]}))
    asyncio.run(store.save("t", {"messages": [{"type": "human", "content": "two"}]}))
    loaded = asyncio.run(store.load("t"))
    assert loaded["messages"][-1].content == "two"
    asyncio.run(store.close())


def test_mcp_schema_pins_are_reloaded(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / ".mcp.json"))
    from iris_ai.mcp.provider import McpPool, _save_schema_pins

    _save_schema_pins({"probe/echo": "abc123"})
    pool = McpPool([])
    assert pool._schema_pins["probe/echo"] == "abc123"
