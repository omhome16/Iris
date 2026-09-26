"""P7 — the tool-surface observability surfaces: `iris tools`, GET /tools, GET /actions.

The blueprints promise is that a policy nobody can inspect is indistinguishable
from one that silently failed. So the CLI and the routes must read the *same*
declarations the engine enforces — these tests check both, and check that an empty
workspace says so instead of inventing output.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from iris_ai.cli.main import app
from iris_ai.cli.tools import run
from iris_ai.config import settings
from iris_ai.toolpolicy import TOOL_DECLARATIONS

runner = CliRunner()


# ── the CLI ────────────────────────────────────────────────────────────────


def test_tools_is_a_real_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "tools" in result.stdout


def test_policy_lists_every_declared_tool_with_class_and_policy():
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0
    for name in TOOL_DECLARATIONS:
        assert name in result.stdout
    assert "control" in result.stdout  # the class is shown
    assert "ask" in result.stdout  # and the policy it resolves to
    assert "visible" in result.stdout or "deferred" in result.stdout


def test_policy_is_the_default_action():
    assert runner.invoke(app, ["tools"]).stdout == runner.invoke(app, ["tools", "policy"]).stdout


def test_policy_shows_computer_as_off_while_the_capability_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "computer_enabled", False)
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 0
    row = next(line for line in result.stdout.splitlines() if "computer" in line and "control" in line)
    assert "off" in row  # absent, not "visible"


def test_policy_flags_a_typo_in_overrides(monkeypatch):
    monkeypatch.setattr(settings, "tool_policy_overrides", "send_messge=deny")
    result = runner.invoke(app, ["tools"])
    assert "send_messge" in result.stdout


def test_policy_reports_a_malformed_override_instead_of_crashing(monkeypatch):
    monkeypatch.setattr(settings, "tool_policy_overrides", "send_message=maybe")
    result = runner.invoke(app, ["tools"])
    assert result.exit_code == 1
    assert "invalid" in result.stdout.lower()
    assert "allow" in result.stdout  # the error states the vocabulary


def test_actions_on_an_empty_workspace_says_so(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    result = runner.invoke(app, ["tools", "actions"])
    assert result.exit_code == 0
    assert "no computer actions" in result.stdout.lower()


def test_actions_reads_the_log(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "actions.jsonl").write_text(
        json.dumps(
            {
                "ts": "2026-09-25T00:00:00+00:00",
                "action": "navigate",
                "target": "https://example.com",
                "ok": True,
                "decision": "allowed",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = runner.invoke(app, ["tools", "actions"])
    assert result.exit_code == 0
    assert "navigate" in result.stdout


def test_run_returns_exit_codes_rather_than_raising():
    assert run("policy") == 0
    assert run("nope") == 2


# ── the routes ─────────────────────────────────────────────────────────────


async def test_tools_route_reports_the_same_policy():
    from iris_ai.api import tools as tools_route

    data = await tools_route(_token=None)
    assert {t["tool"] for t in data["tools"]} == set(TOOL_DECLARATIONS)
    assert data["budget"] == settings.tool_surface_budget
    assert set(data["visible"]) | set(data["deferred"]) == set(TOOL_DECLARATIONS)
    # the readout carries the resolved policy, not just the declaration
    assert all({"class", "policy", "source"} <= set(t) for t in data["tools"])


async def test_actions_route_reads_the_log(tmp_path: Path, monkeypatch):
    from iris_ai.api import actions as actions_route

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "actions.jsonl").write_text(
        json.dumps({"action": "screenshot", "ok": True, "decision": "allowed"}) + "\n",
        encoding="utf-8",
    )
    data = await actions_route(limit=10, _token=None)
    assert data["actions"][0]["action"] == "screenshot"
