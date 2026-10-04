"""The exits that close the open-harness plan: scores, channels, evolve, install."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_ai.catalog.memory.evidence import EvidenceMemory
from iris_ai.channels.messages import InboundMessage, Sender
from iris_ai.channels.runner import ChannelRunner, notify
from iris_ai.engines.plan_execute import PlanExecute
from iris_ai.eval.score import compare_suite, list_suites, refuse_live
from iris_ai.evolve.run import run_evolve
from iris_ai.memory.conflicts import append_conflict, resolve_conflict
from iris_ai.plugins_interop import install_plugin
from iris_ai.sdk.engine import TOOLS
from iris_ai.testing import memory_conformance


def _discord():
    path = Path(__file__).resolve().parents[1] / "examples" / "iris-discord" / "iris_discord" / "channel.py"
    spec = importlib.util.spec_from_file_location("discord_channel_under_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_each_catalog_component_beats_the_default_on_its_suite():
    expected = {
        "context": ("temporal-recall", "temporal-rag"),
        "memory": ("supersession", "evidence-memory"),
        "capture": ("decision-signal", "decision-only"),
        "consolidator": ("conflicts", "conflict-resolver"),
        "persona": ("format", "strict-reviewer"),
    }
    for kind, (suite, name) in expected.items():
        path = next(item for item in list_suites(kind) if item.stem == suite)
        row = compare_suite(path, name, against="default")
        assert row["verdict"] == "pass", row


def test_live_is_refused_for_unapproved_code():
    with pytest.raises(PermissionError):
        refuse_live(True, approved=False)


def test_fixture_world_indexes_the_corpus(tmp_path: Path):
    from iris_ai.eval.world import build_world

    suite = json.loads(next(item for item in list_suites("memory") if item.stem == "supersession").read_text(encoding="utf-8"))
    index = build_world(tmp_path, suite)
    assert index.db_path.is_file()


async def test_evidence_memory_satisfies_the_backend_contract():
    missing = await memory_conformance(EvidenceMemory(path=":memory:"))
    assert missing == []


def test_resolving_a_conflict_edits_memory(tmp_path: Path):
    (tmp_path / "MEMORY.md").write_text("- I prefer Python for AI work.\n", encoding="utf-8")
    conflict_id = append_conflict(
        tmp_path,
        {"existing": "I prefer Python for AI work.", "incoming": "I have moved to Rust for AI work."},
    )
    resolve_conflict(tmp_path, conflict_id, "replace")
    text = (tmp_path / "MEMORY.md").read_text(encoding="utf-8")
    assert "(superseded)" in text
    assert "Rust" in text


def test_dedupe_survives_a_restart(tmp_path: Path):
    ledger = tmp_path / "seen.txt"
    message = InboundMessage(
        channel="discord",
        conversation="dm:1",
        sender=Sender(id="owner"),
        text="hi",
        message_id="m1",
    )
    first = ChannelRunner(channel="discord", owners=("owner",), ledger=ledger)
    assert first.accept(message, now=0) == "owner"
    second = ChannelRunner(channel="discord", owners=("owner",), ledger=ledger)
    assert second.accept(message, now=1) is None


def test_edit_throttle_and_one_turn_at_a_time():
    runner = ChannelRunner(channel="discord", owners=("owner",))
    assert runner.begin("discord:dm:1") is True
    assert runner.begin("discord:dm:1") is False
    runner.end("discord:dm:1")
    assert runner.allow_edit(0.0) is True
    assert runner.allow_edit(0.2) is False
    assert runner.backoff(3) == 4.0


@pytest.mark.asyncio
async def test_a_stranger_cannot_approve():
    class Channel:
        capabilities = frozenset({"approve"})

        async def ask_approval(self, prompt: str) -> str:
            return "approved"

    runner = ChannelRunner(channel="discord", owners=("owner",))
    assert await runner.approval(Channel(), "stranger", "ship it?") == "denied"
    assert await runner.approval(Channel(), "owner", "ship it?") == "approved"


def test_notify_names_the_channel():
    assert notify("discord", "dm:1", "hello")["channel"] == "discord"


def test_discord_maps_a_dm_and_ignores_a_guild_message_without_a_mention():
    module = _discord()
    dm = SimpleNamespace(
        author=SimpleNamespace(id="7", display_name="Ada"),
        content="hello",
        guild=None,
        mentions=[],
        channel=SimpleNamespace(id="9"),
        thread=None,
        id="m",
    )
    inbound = module.inbound_from(dm, bot_id="1")
    assert inbound is not None
    assert inbound.conversation == "dm:9"
    guild = SimpleNamespace(
        author=SimpleNamespace(id="7", display_name="Ada"),
        content="hello",
        guild=SimpleNamespace(id="g"),
        mentions=[],
        channel=SimpleNamespace(id="9"),
        thread=None,
        id="m2",
    )
    assert module.inbound_from(guild, bot_id="1") is None
    assert module.decision_from_interaction("iris-approve") == "approved"


def test_evolve_archives_a_frontier_and_refuses_audit_isolation(tmp_path: Path):
    with pytest.raises(PermissionError):
        run_evolve("context", "temporal-recall", root=tmp_path, allow_audit=False, platform="win32")

    def proposer(index: int) -> list[dict[str, str]]:
        if index:
            return []
        return [{"name": "temporal-rag", "source": "class TemporalRag:\n    pass\n", "notes": "shipped"}]

    report = run_evolve(
        "context",
        "temporal-recall",
        root=tmp_path,
        proposer=proposer,
        iterations=3,
        budget_usd=0.05,
        allow_audit=True,
        platform="win32",
        cost_per_iteration=0.05,
    )
    assert report["activated"] is False
    assert report["frontier"]
    assert (tmp_path / "evolve" / "context-temporal-recall" / "frontier.json").is_file()
    assert report["heldout"]
    assert report["spent_usd"] == pytest.approx(0.05)


def test_a_plugin_installs_skills_and_mcp_with_review_trust(tmp_path: Path):
    root = tmp_path / "plugin"
    skill = root / "skills" / "notes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# notes\n", encoding="utf-8")
    (root / "plugin.json").write_text(json.dumps({"name": "notes", "version": "0.1.0"}), encoding="utf-8")
    (root / "mcp.json").write_text(
        json.dumps({"mcpServers": {"notes": {"command": "${PLUGIN_ROOT}/run"}}}),
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    info = install_plugin(root, workspace)
    assert (workspace / "skills" / "notes" / "SKILL.md").is_file()
    saved = json.loads((workspace / ".mcp.json").read_text(encoding="utf-8"))
    assert saved["mcpServers"]["notes"]["trust"] == "review"
    assert info["mcp"] == ["notes"]


@pytest.mark.asyncio
async def test_plan_execute_routes_a_tool_call_through_tools():
    class Result:
        tool_calls = [{"id": "call_1", "name": "memory_search"}]

    class Services:
        async def complete(self, messages):
            return Result()

    step = await PlanExecute().act({}, Services())
    assert step.next == TOOLS
