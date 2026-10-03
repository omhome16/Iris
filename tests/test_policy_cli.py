"""`iris policy` — the cross-cutting view: classes, overrides, and servers.

The point of the readout is that a decision can be *traced*: which class a tool is
in, what that class defaults to, which override moved it, and what a declared MCP
server's tools would get before anything connects. A policy nobody can read is a
policy nobody can check, so these tests read it the way an owner would.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli_text import flat
from iris_ai.cli.main import app
from iris_ai.cli.policy import run
from iris_ai.config import settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def no_servers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Declared servers are per-test; `tests/conftest.py` already isolates the file."""
    monkeypatch.setattr(settings, "tool_policy_overrides", "")
    return tmp_path


def test_classes_lists_every_class_with_its_default():
    result = runner.invoke(app, ["policy", "classes"])
    assert result.exit_code == 0
    for cls in ("read", "filesystem", "memory_write", "network", "credentialed", "delivery", "control", "external"):
        assert cls in result.stdout
    # The two classes that can act outside Iris's own data default to asking.
    assert "ask" in result.stdout


def test_a_class_override_is_shown_as_an_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "tool_policy_overrides", "external=deny")
    result = runner.invoke(app, ["policy", "overrides"])
    assert result.exit_code == 0
    assert "external" in result.stdout
    assert "every tool in it" in flat(result.stdout)
    assert "deny" in result.stdout


def test_an_unknown_override_is_reported_not_ignored(monkeypatch: pytest.MonkeyPatch):
    """A typo in a security knob must be visible — that is the whole point."""
    monkeypatch.setattr(settings, "tool_policy_overrides", "send_mesage=deny")
    result = runner.invoke(app, ["policy", "overrides"])
    assert result.exit_code == 0
    assert "send_mesage" in result.stdout
    assert "names no tool" in flat(result.stdout)


def test_a_malformed_override_is_an_error_not_a_traceback(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "tool_policy_overrides", "send_message=maybe")
    result = runner.invoke(app, ["policy", "show"])
    assert result.exit_code == 1
    assert "invalid" in result.stdout
    assert "format:" in result.stdout


def test_servers_view_previews_the_rule_before_connecting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """The policy is a function of the declaration, so it can be shown offline."""
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "mine": {"url": "http://127.0.0.1:9/mcp", "trust": "owner"},
                    "theirs": {"url": "http://127.0.0.1:9/mcp"},
                    "parked": {"url": "http://127.0.0.1:9/mcp", "enabled": False},
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "mcp_servers_file", str(path))
    result = runner.invoke(app, ["policy", "servers"])
    assert result.exit_code == 0
    assert "mine" in result.stdout and "owner" in result.stdout
    assert "theirs" in result.stdout
    # An untrusted server: reads allowed by the read-only hint, writes denied.
    assert "allow" in result.stdout and "deny" in result.stdout
    assert "parked" in result.stdout and "off" in result.stdout


def test_servers_view_is_quiet_when_nothing_is_declared(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / "absent.json"))
    result = runner.invoke(app, ["policy", "servers"])
    assert result.exit_code == 0
    assert "No MCP servers declared" in flat(result.stdout)
    assert "config/mcp.json.example" in result.stdout


def test_show_covers_all_three_views(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    path = tmp_path / ".mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"theirs": {"url": "http://127.0.0.1:9/mcp"}}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "mcp_servers_file", str(path))
    result = runner.invoke(app, ["policy"])
    assert result.exit_code == 0
    assert "Capability classes" in flat(result.stdout)
    assert "Server policy" in flat(result.stdout)
    assert "precedence" in result.stdout


def test_run_returns_exit_codes_rather_than_raising():
    assert run("classes") == 0
    assert run("overrides") == 0
    assert run("nope") == 2


def test_the_rule_a_server_gets_matches_what_the_pool_declares(monkeypatch: pytest.MonkeyPatch):
    """The preview must agree with the thing it previews, or it is a nice lie."""
    from iris_ai.mcp import McpServerSpec, McpToolInfo, policy_for

    spec = McpServerSpec(name="theirs", trust="review")
    assert policy_for(McpToolInfo(name="r", read_only=True), spec).policy.value == "allow"
    assert policy_for(McpToolInfo(name="w"), spec).policy.value == "ask"
    strict = McpServerSpec(name="theirs")
    assert policy_for(McpToolInfo(name="w"), strict).policy.value == "deny"
