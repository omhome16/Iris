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


def test_the_component_clock_uses_the_harness_day():
    assert Clock().today() == today()
    assert Clock().now() <= now() + timedelta(seconds=2)


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


def test_repl_help_commands_are_in_the_shared_registry():
    from iris_ai.cli.chat import REPL_HELP
    from iris_ai.commands import names

    for command in ("/exit", "/quit", "/help", "/explain", "/trace", "/conflicts"):
        assert command in REPL_HELP
        assert command in names()


def test_an_unknown_model_is_marked_unpriced(tmp_path: Path):
    from iris_ai.ledger import CostLedger

    ledger = CostLedger(tmp_path / "calls.jsonl")
    ledger.record(model="not-a-priced-model", tier="cheap", prompt_tokens=10, completion_tokens=2)
    row = ledger.rows()[0]
    assert row["unpriced"] is True
    assert row["cost"] == 0.0
