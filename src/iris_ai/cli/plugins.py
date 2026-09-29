"""`iris plugins` — read-only inspection of registered capabilities.

The plug-and-play architecture is only usable if a user can see what is
registered, what config enabled, and where each came from. Three views, all
observation and none needing a running engine:

- **channels** — every registered channel, its source (core or an installed
  plugin), whether config enables it, and the address it would connect to.
- **tools** — every registered tool provider and the tools it contributes.
- **hooks** — the lifecycle events and their built-in subscribers. This is the
  one view that runs plugin code, because a hook's subscribers only exist after
  it has attached to a bus; it attaches to a throwaway bus, and a plugin that
  raises is logged and skipped exactly as it would be at boot.
- **mcp** — the declared external servers, their trust, and what each would
  contribute. Read from the declaration file, so it answers "is this server
  configured, and what would it be allowed to do?" without starting anything;
  `--live` connects and shows what each server actually offers.

Nothing here writes; nothing here constructs a channel or calls a model. A live
MCP readout is the one exception, and it is opt-in.
"""

from __future__ import annotations

import typer
from rich.table import Table

from iris_ai.cli.help_theme import console
from iris_ai.config import settings


def _channels() -> int:
    out = console()
    from iris_ai.channels.registry import CHANNELS, channel_specs, discover_channels

    from_plugins = discover_channels()
    enabled = {spec.name for spec in channel_specs()}
    table = Table(title="Channels", title_style="iris.title", header_style="iris.title")
    table.add_column("channel")
    table.add_column("source")
    table.add_column("enabled")
    table.add_column("address")
    for reg in CHANNELS:
        table.add_row(
            reg.name,
            reg.source,
            "yes" if reg.name in enabled else "no",
            _channel_address(reg.name),
        )
    out.print(table)
    live = [s.name for s in channel_specs()]
    out.print(f"[dim]config enables: {', '.join(live) or 'none'}[/dim]")
    if from_plugins:
        out.print(f"[dim]discovered plugins: {', '.join(from_plugins)}[/dim]")
    out.print(
        "[dim]add one: install a package exposing the `iris_ai.channels` entry point, "
        "then add its name to CHANNELS_ENABLED (or config/harness.toml).[/dim]"
    )
    return 0


def _channel_address(name: str) -> str:
    import os

    env = os.environ.get(f"CHANNEL_{name.upper()}_URL", "")
    if env:
        return env
    if name == "telegram":
        return settings.telegram_mcp_url
    return "-"


def _tools() -> int:
    out = console()
    from iris_ai.toolregistry import TOOL_PROVIDERS, discover_tool_providers

    from_plugins = discover_tool_providers()
    table = Table(title="Tool providers", title_style="iris.title", header_style="iris.title")
    table.add_column("provider")
    table.add_column("source")
    for reg in TOOL_PROVIDERS:
        table.add_row(reg.name, reg.source)
    out.print(table)
    if from_plugins:
        out.print(f"[dim]discovered plugins: {', '.join(from_plugins)}[/dim]")
    out.print(
        "[dim]add one: install a package exposing the `iris_ai.tools` entry point with "
        "an object that has `.name` and `.tools(runtime)`.[/dim]"
    )
    return 0


def _hooks() -> int:
    out = console()
    from iris_ai.guards import GuardName
    from iris_ai.hooks import EVENTS, HookBus, discover_hooks

    table = Table(title="Lifecycle hooks", title_style="iris.title", header_style="iris.title")
    table.add_column("event")
    table.add_column("built-in subscriber")
    builtin = {
        "turn_start": "guards (reset turn detectors)",
        "pre_tool": "guards (budget -> circuit -> spiral)",
        "post_tool": "guards (circuit outcome)",
        "on_error": "-",
        "turn_end": "guards (bank day budget)",
    }
    for event in EVENTS:
        table.add_row(event, builtin.get(event, "-"))
    out.print(table)

    # Installed plugins are attached to a throwaway bus here, so the readout
    # answers "what would subscribe on a real boot" without a running engine.
    # A plugin's own handlers are invisible until it attaches — this is the only
    # honest place to learn that it exists and where its hooks landed.
    probe = HookBus()
    attached = discover_hooks(probe)
    if attached:
        out.print(f"[dim]discovered plugins: {', '.join(attached)}[/dim]")
        for event, names in probe.describe().items():
            if names:
                out.print(f"[dim]  {event}: {', '.join(names)}[/dim]")
    order = " -> ".join(g.value for g in GuardName)
    out.print(f"[dim]pre-tool refusal order: {order}[/dim]")
    out.print(
        "[dim]add one: register a handler on the HookBus (`iris_ai.hooks`) for any event; "
        "a raising hook is skipped, never propagated.[/dim]"
    )
    return 0


def _mcp(live: bool = False) -> int:
    """Declared MCP servers, and optionally what they actually offer.

    Declared is the default because it is the question that matters most often:
    "I wrote this server into `.mcp.json` and the tool never showed up" is
    answered by the declaration *and* by `connected`, and connecting to find out
    would mean starting every server just to read a config file.
    """
    out = console()
    from iris_ai.mcp import McpConfigError, load_servers

    try:
        specs = load_servers()
    except McpConfigError as exc:
        out.print(f"[iris.fail]MCP config is not usable:[/iris.fail] {exc}")
        return 1

    if not specs:
        out.print(
            f"[dim]No MCP servers declared. {settings.mcp_servers_file} does not exist "
            "(or declares none) — copy config/mcp.json.example to get started.[/dim]"
        )
        return 0

    rows = _mcp_rows(specs, live=live)
    table = Table(title="MCP servers", title_style="iris.title", header_style="iris.title")
    table.add_column("server")
    table.add_column("transport")
    table.add_column("trust")
    table.add_column("approval")
    table.add_column("state")
    for row in rows:
        if row["connected"]:
            state = "connected"
        elif row["error"]:
            state = row["error"]
            if row.get("retryable"):
                state = f"retrying — {state}"
        else:
            state = "declared"
        table.add_row(
            row["server"],
            row["transport"],
            row["trust"],
            row["approval"],
            f"[iris.fail]{state}[/iris.fail]" if row["error"] else state,
        )
    out.print(table)

    for row in rows:
        for tool in row["tools"]:
            style = {"allow": "iris.ok", "ask": "iris.warn", "deny": "iris.fail"}[tool["policy"]]
            out.print(f"  [{style}]{tool['policy']}[/{style}] {tool['tool']} — {tool['reason']}")

    offline = [s.name for s in specs if not s.enabled]
    if offline:
        out.print(f"[dim]switched off in config: {', '.join(offline)}[/dim]")
    if not live:
        out.print(
            "[dim]tool lists come from a connection: re-run with --live to connect and "
            "show what each server offers, and the policy each tool would get.[/dim]"
        )
    out.print(
        "[dim]edit the file (MCP_SERVERS_FILE), or set `trust: owner` / "
        "`approval: never` per server; `tool_policy_overrides` can deny one tool by name.[/dim]"
    )
    return 0


def _mcp_rows(specs: list, *, live: bool) -> list[dict]:
    """One row per declared server: trust from the spec, tools from a connection.

    The offline rows carry the *declaration* only. Filling in a tool list without
    connecting would mean inventing it, and the whole point of this view is that
    "declared", "connected" and "what it offers" are three different answers.
    """
    enabled = [spec for spec in specs if spec.enabled]
    rows = [
        {
            "server": spec.name,
            "transport": spec.transport,
            "trust": spec.trust,
            "approval": spec.approval,
            "connected": False,
            "error": "",
            "tools": [],
        }
        for spec in enabled
    ]
    if not live:
        return rows

    import asyncio

    from iris_ai.mcp.provider import McpPool

    async def _probe() -> list[dict]:
        async with McpPool(enabled) as pool:
            return pool.status()

    try:
        return asyncio.run(_probe())
    except RuntimeError:
        # A caller embedding the CLI inside a running loop. Reported, not swallowed:
        # the rows would otherwise look like "declared and nothing else".
        console().print("[iris.fail]--live needs no running event loop; use `iris doctor` instead[/iris.fail]")
        return rows


def run(action: str = "channels", *, live: bool = False) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    try:
        match action:
            case "channels" | "list":
                return _channels()
            case "tools":
                return _tools()
            case "hooks":
                return _hooks()
            case "mcp":
                return _mcp(live=live)
            case _:
                out.print(
                    f"[iris.fail]unknown action {action!r}[/iris.fail] — use channels, tools, hooks or mcp"
                )
                return 2
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - an inspection command must not traceback
        out.print(f"[iris.fail]could not inspect capabilities:[/iris.fail] {exc}")
        return 1
