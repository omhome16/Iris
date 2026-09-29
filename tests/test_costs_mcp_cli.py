"""`iris costs` and `iris mcp` — the ledger readout and the declaration verbs.

Both are *editing or reading* commands, so the tests follow one rule each:

- costs: never print a confident number that the ledger does not support (an
  unpriced model makes a total a lower bound, and an empty ledger says so);
- mcp: never write a file the loader would reject, and never default a server
  into trust it was not given.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iris_ai.cli.main import app
from iris_ai.cli.mcp import run as mcp_run
from iris_ai.config import settings

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / ".mcp.json"))
    return tmp_path


def _ledger_line(tmp_path: Path, *, ts: str, model: str, cost: float, prompt: int = 100) -> None:
    path = tmp_path / "config" / "llm_calls.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": ts,
                    "model": model,
                    "cost": cost,
                    "prompt_tokens": prompt,
                    "completion_tokens": 20,
                    "cached_tokens": 0,
                }
            )
            + "\n"
        )


# ── costs ───────────────────────────────────────────────────────────────────


def test_an_empty_ledger_says_so_instead_of_zero(tmp_path: Path):
    result = runner.invoke(app, ["costs"])
    assert result.exit_code == 0
    assert "No model calls recorded yet" in result.stdout


def test_the_summary_groups_by_model_and_names_unpriced_ones(tmp_path: Path):
    _ledger_line(tmp_path, ts="2026-09-26T10:00:00+00:00", model="gemini/gemini-3.5-flash", cost=0.02)
    _ledger_line(tmp_path, ts="2026-09-27T10:00:00+00:00", model="some-local-model", cost=0.0)
    result = runner.invoke(app, ["costs"])
    assert result.exit_code == 0
    assert "gemini/gemini-3.5-flash" in result.stdout
    assert "some-local-model" in result.stdout
    # The honesty rule: a $0.00 row for an unpriced model must be called out.
    assert "lower bound" in result.stdout
    assert "some-local-model" in result.stdout


def test_a_explicitly_priced_ledger_is_reported_as_measured(tmp_path: Path):
    _ledger_line(tmp_path, ts="2026-09-27T10:00:00+00:00", model="gemini/gemini-3.5-flash", cost=0.05)
    result = runner.invoke(app, ["costs", "summary"])
    assert result.exit_code == 0
    assert "lower bound" not in result.stdout
    assert "$0.05" in result.stdout


def test_daily_and_weekly_roll_ups(tmp_path: Path):
    _ledger_line(tmp_path, ts="2026-09-27T10:00:00+00:00", model="gemini/gemini-3.5-flash", cost=0.03)
    daily = runner.invoke(app, ["costs", "daily", "--days", "3"])
    assert daily.exit_code == 0
    assert "2026-09-27" in daily.stdout
    weekly = runner.invoke(app, ["costs", "weekly"])
    assert weekly.exit_code == 0
    assert "-W" in weekly.stdout  # ISO week label


def test_costs_returns_exit_codes(tmp_path: Path):
    from iris_ai.cli.costs import run

    _ledger_line(tmp_path, ts="2026-09-27T10:00:00+00:00", model="gemini/gemini-3.5-flash", cost=0.01)
    assert run("summary") == 0
    assert run("daily") == 0
    assert run("weekly") == 0
    assert run("nope") == 2


# ── mcp ─────────────────────────────────────────────────────────────────────


def test_add_writes_a_server_the_loader_accepts(tmp_path: Path):
    result = runner.invoke(app, ["mcp", "add", "wiki", "--url", "http://127.0.0.1:9/mcp"])
    assert result.exit_code == 0
    assert "added" in result.stdout

    from iris_ai.mcp import load_servers

    (spec,) = load_servers(tmp_path / ".mcp.json", environ={})
    assert spec.name == "wiki"
    assert spec.transport == "http"
    # The default is the safe one, and the command says so out loud.
    assert spec.trust == "untrusted"
    assert "untrusted" in result.stdout


def test_add_preserves_what_was_already_declared(tmp_path: Path):
    (tmp_path / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"existing": {"url": "http://127.0.0.1:9/mcp"}}}),
        encoding="utf-8",
    )
    assert mcp_run("add", name="second", url="http://127.0.0.1:8/mcp", trust="owner", args="", approval="auto", enabled=True) == 0
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert sorted(data["mcpServers"]) == ["existing", "second"]
    assert data["mcpServers"]["second"]["trust"] == "owner"


def test_add_refuses_a_declaration_the_loader_would_reject(tmp_path: Path):
    """Writing an invalid file would be worse than refusing the edit."""
    result = runner.invoke(app, ["mcp", "add", "wiki", "--url", "http://x/mcp", "--trust", "friend"])
    assert result.exit_code == 1
    assert "not valid" in result.stdout
    assert not (tmp_path / ".mcp.json").exists()


def test_add_needs_exactly_one_transport(tmp_path: Path):
    both = runner.invoke(app, ["mcp", "add", "wiki", "--url", "http://x/mcp", "--command", "uvx"])
    assert both.exit_code == 2
    neither = runner.invoke(app, ["mcp", "add", "wiki"])
    assert neither.exit_code == 2


def test_add_can_park_a_server(tmp_path: Path):
    assert (
        mcp_run(
            "add",
            name="later",
            url="http://127.0.0.1:9/mcp",
            trust="untrusted",
            approval="auto",
            args="",
            enabled=False,
        )
        == 0
    )
    from iris_ai.mcp import enabled_servers, load_servers

    assert [s.name for s in load_servers(environ={})] == ["later"]
    assert enabled_servers() == []


def test_remove_reports_whether_it_was_there(tmp_path: Path):
    mcp_run("add", name="wiki", url="http://127.0.0.1:9/mcp", trust="untrusted", approval="auto", args="", enabled=True)
    assert mcp_run("remove", name="wiki") == 0
    assert mcp_run("remove", name="wiki") == 1  # already gone
    data = json.loads((tmp_path / ".mcp.json").read_text(encoding="utf-8"))
    assert data["mcpServers"] == {}


def test_list_delegates_to_the_plugins_readout(tmp_path: Path):
    mcp_run("add", name="wiki", url="http://127.0.0.1:9/mcp", trust="untrusted", approval="auto", args="", enabled=True)
    result = runner.invoke(app, ["mcp", "list"])
    assert result.exit_code == 0
    assert "wiki" in result.stdout


def test_test_reports_a_server_that_is_not_declared(tmp_path: Path):
    result = runner.invoke(app, ["mcp", "test", "ghost"])
    assert result.exit_code == 1
    assert "not declared" in result.stdout


def test_test_connects_one_server_in_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`test` is the narrow question — is *this* server working — so it connects
    only this one, and reports its tools with the policy each would get."""
    from mcp.server.mcpserver import MCPServer
    from mcp.types import ToolAnnotations

    from iris_ai.mcp import client as client_mod
    from iris_ai.mcp import provider as provider_mod

    mcp_run(
        "add",
        name="probe",
        url="http://127.0.0.1:9/mcp",
        trust="owner",
        approval="auto",
        args="",
        enabled=True,
    )
    server = MCPServer("probe")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    real = client_mod.open_server
    monkeypatch.setattr(provider_mod, "open_server", lambda spec: real(spec, server=server))
    result = runner.invoke(app, ["mcp", "test", "probe"])

    assert result.exit_code == 0, result.stdout
    assert "probe/echo" in result.stdout
    assert "allow" in result.stdout


def test_mcp_returns_exit_codes_rather_than_raising(tmp_path: Path):
    assert mcp_run("list") == 0
    assert mcp_run("add") == 2
    assert mcp_run("remove") == 2
    assert mcp_run("test") == 2
    assert mcp_run("nope") == 2
