"""The capability surfaces: `iris plugins` over channels, tool providers, hooks
and MCP servers. The registry is the point of the plug-and-play layer, so its
readout must show what is registered and where it came from — not invent output.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from cli_text import flat
from iris_ai.cli.main import app
from iris_ai.cli.plugins import run
from iris_ai.config import settings

runner = CliRunner()


def test_plugins_is_a_real_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "plugins" in result.stdout


def test_channels_lists_the_builtin_and_its_address():
    result = runner.invoke(app, ["plugins", "channels"])
    assert result.exit_code == 0
    assert "telegram" in result.stdout
    assert "core" in result.stdout


def test_tools_lists_the_core_provider():
    result = runner.invoke(app, ["plugins", "tools"])
    assert result.exit_code == 0
    assert "core" in result.stdout


def test_hooks_shows_the_guard_order():
    result = runner.invoke(app, ["plugins", "hooks"])
    assert result.exit_code == 0
    assert "pre_tool" in result.stdout
    assert "budget" in result.stdout and "spiral" in result.stdout


def test_channels_is_the_default_action():
    assert runner.invoke(app, ["plugins"]).stdout == runner.invoke(app, ["plugins", "channels"]).stdout


def test_run_returns_exit_codes_rather_than_raising():
    assert run("channels") == 0
    assert run("tools") == 0
    assert run("hooks") == 0
    assert run("nope") == 2


# ── MCP servers ─────────────────────────────────────────────────────────────


def _declare(tmp_path: Path, servers: dict) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")


def test_mcp_says_so_when_nothing_is_declared(monkeypatch, tmp_path: Path):
    """The common case, answered with the file to copy rather than a bare table."""
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / "absent.json"))
    result = runner.invoke(app, ["plugins", "mcp"])
    assert result.exit_code == 0
    assert "No MCP servers declared" in flat(result.stdout)
    assert "config/mcp.json.example" in result.stdout


def test_mcp_shows_each_declared_server_and_its_trust(monkeypatch, tmp_path: Path):
    _declare(
        tmp_path,
        {
            "wiki": {"url": "http://127.0.0.1:8100/mcp", "trust": "owner", "approval": "always"},
            "notes": {"command": "uvx", "args": ["mcp-server-notes"]},
            "off": {"command": "uvx", "enabled": False},
        },
    )
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / ".mcp.json"))
    result = runner.invoke(app, ["plugins", "mcp"])

    assert result.exit_code == 0
    assert "wiki" in result.stdout and "owner" in result.stdout
    assert "always" in result.stdout
    assert "notes" in result.stdout and "stdio" in result.stdout
    # A server switched off is reported as switched off, not as missing.
    assert "switched off in config: off" in flat(result.stdout)
    assert "--live" in result.stdout


def test_mcp_live_reports_what_each_server_offers(monkeypatch, tmp_path: Path):
    """`--live` prints the tool list and the *policy* each tool would get."""
    _declare(tmp_path, {"wiki": {"url": "http://127.0.0.1:8100/mcp", "trust": "owner"}})
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / ".mcp.json"))

    class FakePool:
        def __init__(self, specs):
            self._specs = list(specs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return None

        def status(self):
            return [
                {
                    "server": "wiki",
                    "transport": "http",
                    "trust": "owner",
                    "approval": "auto",
                    "connected": True,
                    "error": "",
                    "tools": [
                        {
                            "tool": "wiki/read",
                            "read_only": True,
                            "policy": "allow",
                            "reason": "wiki: read-only per the server's own hint",
                        },
                        {
                            "tool": "wiki/write",
                            "read_only": False,
                            "policy": "ask",
                            "reason": "wiki: trusted server, tool is not read-only",
                        },
                    ],
                }
            ]

    import iris_ai.mcp.provider as provider

    monkeypatch.setattr(provider, "McpPool", FakePool)
    result = runner.invoke(app, ["plugins", "mcp", "--live"])

    assert result.exit_code == 0
    assert "connected" in result.stdout
    assert "wiki/read" in result.stdout and "wiki/write" in result.stdout
    assert "ask" in result.stdout


def test_mcp_reports_a_broken_config_instead_of_tracebacking(monkeypatch, tmp_path: Path):
    """A config the owner believes in must be reported, not swallowed."""
    (tmp_path / ".mcp.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / ".mcp.json"))
    result = runner.invoke(app, ["plugins", "mcp"])
    assert result.exit_code == 1
    assert "not usable" in flat(result.stdout)
