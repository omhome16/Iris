"""P5 — the observability surfaces: `iris agents` and `GET /agents`.

The blueprint promises "which agent decided what". That is only true if the
surfaces read the *same* declarations the runner enforces and the *same* trace
store the runtime writes — so these tests check both, and check that a workspace
with nothing to show says so instead of inventing output.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from iris.cli.agents import handoffs_from_traces, run
from iris.cli.main import app

runner = CliRunner()


def test_agents_is_a_real_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "agents" in result.stdout


def test_roles_lists_the_declared_pack():
    result = runner.invoke(app, ["agents", "roles"])
    assert result.exit_code == 0
    assert "researcher" in result.stdout
    assert "critic" in result.stdout
    assert "memory_search" in result.stdout  # the allowlist is shown, not implied


def test_roles_is_the_default_action():
    assert runner.invoke(app, ["agents"]).stdout == runner.invoke(app, ["agents", "roles"]).stdout


def test_show_prints_the_prompt_and_the_bounds():
    result = runner.invoke(app, ["agents", "show", "critic"])
    assert result.exit_code == 0
    assert "unsupported" in result.stdout  # from the critic's own instructions
    assert "per claim" in result.stdout.replace("\n", " ").lower() or "claim" in result.stdout


def test_show_unknown_role_exits_non_zero_and_names_the_real_ones():
    result = runner.invoke(app, ["agents", "show", "astrologer"])
    assert result.exit_code == 1
    assert "researcher" in result.stdout  # the error names what does exist


def test_show_without_a_name_is_a_usage_error():
    result = runner.invoke(app, ["agents", "show"])
    assert result.exit_code == 2


def test_an_unknown_action_is_a_usage_error():
    result = runner.invoke(app, ["agents", "summon"])
    assert result.exit_code == 2
    assert "roles" in result.stdout


def test_handoffs_on_an_empty_workspace_says_so(tmp_path: Path, monkeypatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    result = runner.invoke(app, ["agents", "handoffs"])
    assert result.exit_code == 0
    assert "no agent decisions" in result.stdout.lower()


def test_handoffs_reads_delegations_out_of_the_traces(tmp_path: Path, monkeypatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "traces.jsonl").write_text(
        json.dumps(
            {
                "session_id": "cli",
                "judgment": {
                    "events": [
                        {"kind": "handoff", "from": "researcher", "to": "lead", "claims": 2,
                         "sourced": 1, "unsourced": 1, "tokens": 812, "ms": 640},
                        {"kind": "answer_check", "verdict": "partial", "grounded": 0.31},
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rows = handoffs_from_traces(10)
    assert [kind for kind, _ in rows] == ["handoff", "answer_check"]
    result = runner.invoke(app, ["agents", "handoffs"])
    assert result.exit_code == 0
    assert "researcher" in result.stdout
    assert "812" in result.stdout


def test_run_returns_exit_codes_rather_than_raising():
    assert run("roles") == 0
    assert run("show", "nope") == 1
    assert run("nope") == 2


# ── the API route ────────────────────────────────────────────────────────


async def test_the_agents_route_reports_the_declared_pack(tmp_path: Path, monkeypatch):
    from iris.api import agents_list
    from iris.config import settings

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    data = await agents_list(limit=10, _token=None)
    assert {r["name"] for r in data["roles"]} == {"researcher", "critic"}
    assert data["roles"][0]["tools"]  # the allowlist is exposed, not implied
    assert data["decisions"] == []
    assert "enabled" in data


async def test_the_agents_route_surfaces_recent_decisions(tmp_path: Path, monkeypatch):
    from iris.api import agents_list
    from iris.config import settings

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    config = tmp_path / "config"
    config.mkdir(parents=True)
    (config / "traces.jsonl").write_text(
        json.dumps(
            {
                "session_id": "api",
                "judgment": {
                    "events": [
                        {"kind": "handoff", "from": "researcher", "to": "lead", "claims": 1},
                        {"kind": "web", "url": "https://example.com"},  # not a decision
                    ]
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    data = await agents_list(limit=10, _token=None)
    assert [d["from"] for d in data["decisions"]] == ["researcher"]
    assert "web" not in {d["kind"] for d in data["decisions"]}
