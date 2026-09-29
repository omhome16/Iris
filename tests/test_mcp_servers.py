"""`iris_ai.mcp` — declared servers, namespacing, and the trust model.

The client tests run against the SDK's **in-process transport**, so a real
handshake, a real `tools/list` and a real `tools/call` happen with no subprocess
and no port. That is deliberate: the alternative (spawning a stdio server) cannot
run under the Windows selector loop the CLI and the API select, and a test that
only passes in some event loops is worse than no test.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from iris_ai.config import settings
from iris_ai.mcp import (
    McpConfigError,
    McpServerSpec,
    McpToolInfo,
    enabled_servers,
    load_servers,
    namespaced,
    policy_for,
)
from iris_ai.mcp.client import McpUnavailable, open_server
from iris_ai.toolpolicy import Policy

USEFUL = "untrusted"  # the default, spelled once so the intent is readable


def _declare(tmp_path: Path, servers: dict) -> Path:
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")
    return path


def _spec(**kwargs) -> McpServerSpec:
    return McpServerSpec(name="probe", **kwargs)


def _tool(**kwargs) -> McpToolInfo:
    return McpToolInfo(name="wipe", **kwargs)


# ── declaration ─────────────────────────────────────────────────────────────


def test_servers_load_from_the_shape_an_owner_already_has(tmp_path: Path):
    """`.mcp.json` is the ecosystem's file, so a config can be brought over as-is."""
    path = _declare(
        tmp_path,
        {
            "notes": {"command": "uvx", "args": ["mcp-server-notes", "--strict"]},
            "wiki": {"url": "http://127.0.0.1:8100/mcp", "trust": "owner"},
        },
    )
    specs = {spec.name: spec for spec in load_servers(path, environ={})}

    assert specs["notes"].transport == "stdio"  # inferred from `command`
    assert specs["notes"].args == ("mcp-server-notes", "--strict")
    assert specs["wiki"].transport == "http"  # inferred from `url`
    assert specs["wiki"].trust == "owner"
    # Untrusted and unapproved unless the owner said otherwise: the default has
    # to be the safe one, because the common case is a server nobody has vetted.
    assert specs["notes"].trust == USEFUL
    assert specs["notes"].approval == "auto"


def test_a_missing_file_is_no_servers_not_an_error(tmp_path: Path):
    assert load_servers(tmp_path / "absent.json") == []


def test_the_shipped_example_config_loads_and_connects_nothing():
    """JSON has no comments, so the example is kept *valid and inert* instead.

    Copying it must not connect anything (`enabled: false` on every server), and
    it must not raise. The prose explaining each key lives in DOCS.md —
    a `_notes` key inside the file would be an unknown-key error, which is exactly
    what the loader should do with it.
    """
    template = Path("config/mcp.json.example")
    specs = load_servers(template, environ={"MCP_NOTES_TOKEN": "from-the-environment"})

    assert specs, "the example must declare something for it to be an example"
    assert all(not spec.enabled for spec in specs)
    # Nothing is switched on, so the boot path connects nothing — and does not
    # demand the example's token be in this process's environment.
    assert [spec.name for spec in load_servers(template, environ={}) if spec.enabled] == []
    # Both transports are shown, because the difference (and the Windows caveat
    # on stdio) is the first thing an owner needs to know.
    assert {spec.transport for spec in specs} == {"http", "stdio"}
    # `${VAR}` is resolved, never written down: the example must not carry a secret.
    assert specs[1].env == {"NOTES_TOKEN": "from-the-environment"}


def test_an_unreadable_file_is_one_clear_error(tmp_path: Path):
    path = tmp_path / ".mcp.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(McpConfigError) as exc:
        load_servers(path)
    assert "not valid JSON" in str(exc.value)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"command": "uvx", "trus": "owner"}, "unknown key"),
        ({"url": "http://x/mcp", "transport": "carrier-pigeon"}, "unknown transport"),
        ({"url": "http://x/mcp", "trust": "friend"}, "unknown trust"),
        ({"url": "http://x/mcp", "approval": "maybe"}, "unknown approval"),
        ({"transport": "stdio"}, "needs `command`"),
        ({"transport": "http"}, "needs `url`"),
        ({"command": "uvx", "env": ["A=1"]}, "`env` must be an object"),
    ],
)
def test_a_broken_declaration_names_the_server_and_the_problem(tmp_path: Path, entry, expected):
    """A typo must not look like a working connection to a server that is not there."""
    path = _declare(tmp_path, {"notes": entry})
    with pytest.raises(McpConfigError) as exc:
        load_servers(path)
    assert "server 'notes'" in str(exc.value)
    assert expected in str(exc.value)


def test_all_three_transports_are_accepted_and_handed_over_correctly(tmp_path: Path):
    """`http` (streamable), `sse` (the older HTTP transport) and `stdio`.

    The transport is chosen from the declaration and handed to the SDK as what it
    expects: a URL string for streamable HTTP, a stream pair for SSE, a spawn for
    stdio. Getting these mixed up is the kind of mistake that only shows up
    against a real server, so the mapping is pinned here.
    """
    from iris_ai.mcp.client import _target

    # A plain URL: the SDK's streamable-HTTP client takes the string itself.
    assert _target(_spec(transport="http", url="http://127.0.0.1:8100/mcp")) == "http://127.0.0.1:8100/mcp"
    # An async context manager yielding (read, write) streams.
    assert hasattr(_target(_spec(transport="sse", url="http://127.0.0.1:8200/sse")), "__aenter__")

    loaded = load_servers(
        _declare(tmp_path, {"legacy": {"url": "http://127.0.0.1:8200/sse", "transport": "sse"}}),
        environ={},
    )
    assert loaded[0].transport == "sse"


def test_a_url_transport_without_a_url_is_refused(tmp_path: Path):
    with pytest.raises(McpConfigError) as exc:
        load_servers(_declare(tmp_path, {"legacy": {"transport": "sse"}}), environ={})
    assert "sse server needs `url`" in str(exc.value)


def test_a_transport_this_sdk_cannot_speak_is_refused_not_downgraded(tmp_path: Path):
    """`ws` is a real MCP transport, but this SDK ships no websocket *client*.

    Accepting the name and connecting over something else would be a silent
    downgrade of exactly the kind the validator exists to prevent.
    """
    path = _declare(tmp_path, {"socket": {"url": "ws://127.0.0.1:8300", "transport": "ws"}})
    with pytest.raises(McpConfigError) as exc:
        load_servers(path, environ={})
    assert "unknown transport" in str(exc.value)


def test_env_values_are_resolved_from_the_environment_not_the_file(tmp_path: Path):
    """A token belongs in the environment; the file is meant to be committable."""
    path = _declare(
        tmp_path,
        {"notes": {"command": "uvx", "env": {"NOTES_TOKEN": "${MCP_NOTES_TOKEN}"}}},
    )
    specs = load_servers(path, environ={"MCP_NOTES_TOKEN": "s3cret"})
    assert specs[0].env == {"NOTES_TOKEN": "s3cret"}


def test_an_unset_variable_fails_naming_the_variable_and_never_the_value(tmp_path: Path):
    path = _declare(
        tmp_path,
        {"notes": {"command": "uvx", "env": {"NOTES_TOKEN": "${MCP_NOTES_TOKEN}"}}},
    )
    with pytest.raises(McpConfigError) as exc:
        load_servers(path, environ={"MCP_OTHER": "nope"})
    assert "MCP_NOTES_TOKEN" in str(exc.value)
    assert "nope" not in str(exc.value)


def test_a_disabled_server_is_declared_but_not_connected(tmp_path: Path):
    path = _declare(tmp_path, {"notes": {"command": "uvx", "enabled": False}})
    assert [spec.name for spec in load_servers(path, environ={})] == ["notes"]
    assert enabled_servers(path) == []


def test_a_switched_off_server_does_not_need_its_secret_yet(tmp_path: Path):
    """Parking a declaration must not require the token it points at.

    The placeholder is left as written, so *enabling* it fails loudly then — the
    moment the token is actually needed — instead of at boot for a server nobody
    is using.
    """
    path = _declare(
        tmp_path,
        {"notes": {"command": "uvx", "enabled": False, "env": {"TOKEN": "${NOT_SET_ANYWHERE}"}}},
    )
    (spec,) = load_servers(path, environ={})
    assert spec.env == {"TOKEN": "${NOT_SET_ANYWHERE}"}

    # The same declaration, switched on, is an error naming the variable.
    path = _declare(
        tmp_path,
        {"notes": {"command": "uvx", "env": {"TOKEN": "${NOT_SET_ANYWHERE}"}}},
    )
    with pytest.raises(McpConfigError) as exc:
        load_servers(path, environ={})
    assert "NOT_SET_ANYWHERE" in str(exc.value)


def test_the_configured_file_is_the_declared_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _declare(tmp_path, {"notes": {"command": "uvx"}})
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / ".mcp.json"))
    assert [spec.name for spec in enabled_servers()] == ["notes"]


# ── namespacing and trust ───────────────────────────────────────────────────


def test_tools_are_namespaced_by_their_server():
    """Two servers may both ship `search`; a trace must still say which answered."""
    assert namespaced("notes", "search") == "notes/search"


def test_an_untrusted_servers_write_tool_is_denied():
    """Phase 3's safety claim: the *default* cannot write on first contact."""
    decision = policy_for(_tool(read_only=False), _spec(trust="untrusted"))
    assert decision.policy is Policy.DENY
    assert "untrusted" in decision.reason


def test_a_read_only_hint_is_enough_to_allow_a_read():
    """A hint is self-reported, so it buys reads — and nothing more."""
    read = policy_for(_tool(read_only=True), _spec(trust="untrusted"))
    assert read.policy is Policy.ALLOW
    assert "read-only" in read.reason


def test_a_trusted_servers_write_asks_the_owner():
    assert policy_for(_tool(), _spec(trust="owner")).policy is Policy.ASK


def test_an_explicit_never_cannot_open_an_untrusted_write():
    """`deny` is a floor: trust is not something a per-server setting can buy back."""
    decision = policy_for(_tool(), _spec(trust="untrusted", approval="never"))
    assert decision.policy is Policy.DENY


def test_explicit_approval_settings_beat_the_read_only_default():
    always = policy_for(_tool(read_only=True), _spec(trust="owner", approval="always"))
    assert always.policy is Policy.ASK  # "always" means every call, reads included
    never = policy_for(_tool(), _spec(trust="owner", approval="never"))
    assert never.policy is Policy.ALLOW


# ── the client, against a real server over the in-process transport ─────────


def _probe_server() -> MCPServer:
    server = MCPServer("probe")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def echo(text: str) -> str:
        """Return the text unchanged."""
        return text

    @server.tool(annotations=ToolAnnotations(destructive_hint=True))
    def wipe(path: str) -> str:
        """Pretend to delete something."""
        return f"wiped {path}"

    return server


async def test_a_real_server_lists_and_calls_its_tools():
    async with open_server(_spec(trust="owner"), server=_probe_server()) as connection:
        tools = {tool.name: tool for tool in await connection.tools()}
        assert set(tools) == {"echo", "wipe"}
        assert tools["echo"].read_only is True
        assert tools["wipe"].read_only is False
        assert tools["echo"].input_schema.get("type") == "object"

        assert json.loads(await connection.call("echo", {"text": "hi"}))["text"] == "hi"


async def test_the_trust_decision_uses_what_the_server_actually_advertised():
    """End to end: the hints come from the server, the verdict from `policy_for`."""
    async with open_server(_spec(trust="untrusted"), server=_probe_server()) as connection:
        verdicts = {
            tool.name: policy_for(tool, connection.spec).policy for tool in await connection.tools()
        }
    assert verdicts == {"echo": Policy.ALLOW, "wipe": Policy.DENY}


async def test_a_failed_call_is_a_result_not_an_exception():
    """A tool boundary reports failure; raising would hide the reason from the model."""
    server = MCPServer("probe")

    @server.tool()
    def broken() -> str:
        raise RuntimeError("no such thing")

    async with open_server(_spec(), server=server) as connection:
        payload = json.loads(await connection.call("broken", {}))
    assert payload["ok"] is False
    assert payload["tool"] == "probe/broken"
    assert "no such thing" in payload["error"]


def test_an_unreachable_server_is_named_and_does_not_raise_anything_else():
    """A down server degrades its own capability; the caller keeps its boot."""

    async def scenario() -> None:
        # A command that cannot exist: the spawn fails on every platform, and on
        # Windows under the selector loop the subprocess call itself is the
        # failure (see the module docstring).
        async with open_server(_spec(transport="stdio", command="iris-does-not-exist")):
            pass  # pragma: no cover - reaching the body would mean it connected

    with pytest.raises(McpUnavailable) as exc:
        asyncio.run(scenario())
    assert str(exc.value).startswith("probe:")
