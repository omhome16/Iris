"""`iris policy` — the whole policy in one place, and where each part came from.

The rule that makes Iris's safety story checkable is that policy is *data*:
classes have defaults, overrides may only tighten, `deny` wins, and every
decision names its source. `iris tools policy` reads the tool half of that in
detail. This command is the cross-cutting view the redesign asks for, because a
tool is no longer the only thing that can be refused:

- **classes** — what each capability class defaults to, how many tools are in it,
  and whether an override has moved it;
- **overrides** — every key in `TOOL_POLICY_OVERRIDES`, what it applies to
  (a tool, a class), and the keys that name nothing (a typo in a security knob
  must be visible, not silently ignored);
- **servers** — every declared MCP server, its trust and approval, and the policy
  a read-only and a non-read-only tool would each get. Two *hypothetical* tools,
  used as a rule preview: connecting to find out would mean starting every server
  to read a config file, and the rule is a pure function of the declaration.

Read-only. Nothing here constructs a server, spins an engine, or prints a secret.
"""

from __future__ import annotations

from collections import Counter

from rich.table import Table

from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.toolpolicy import (
    CLASS_DEFAULTS,
    PolicyError,
    ToolClass,
    parse_overrides,
    unknown_overrides,
)

_STYLE = {"allow": "iris.ok", "ask": "iris.warn", "deny": "iris.fail"}


def _styled(policy: str) -> str:
    style = _STYLE.get(policy, "")
    return f"[{style}]{policy}[/{style}]" if style else policy


def _overrides() -> tuple[dict, int]:
    """Parsed overrides, or `({}, exit_code)` when `.env` has a broken one."""
    out = console()
    try:
        return parse_overrides(settings.tool_policy_overrides), 0
    except PolicyError as exc:
        # The engine refuses to boot on this too, so the CLI says why rather than
        # raising a traceback over a typo.
        out.print(f"[iris.fail]tool_policy_overrides is invalid:[/iris.fail] {exc}")
        out.print("  format: name or class = allow | ask | deny, comma-separated")
        return {}, 1


def _classes() -> int:
    out = console()
    rules, code = _overrides()
    if code:
        return code

    from iris_ai.toolpolicy import EXTERNAL_TOOLS, TOOL_DECLARATIONS

    counts: Counter[str] = Counter(
        [d.cls.value for d in TOOL_DECLARATIONS.values()]
        + [t.cls.value for t in EXTERNAL_TOOLS.values()]
    )
    table = Table(title="Capability classes", title_style="iris.title", header_style="iris.title")
    table.add_column("class")
    table.add_column("default")
    table.add_column("override")
    table.add_column("tools")
    for cls in ToolClass:
        default = CLASS_DEFAULTS[cls]
        override = rules.get(cls.value)
        table.add_row(
            cls.value,
            _styled(default.value),
            _styled(override.value) if override else "[dim]-[/dim]",
            str(counts.get(cls.value, 0)),
        )
    out.print(table)
    out.print(
        "[dim]precedence: class default < a tool's own source (an MCP server's trust) "
        "< a class override < a per-tool override. `deny` wins outright at every level, "
        "so an override can tighten and never loosen.[/dim]"
    )
    return 0


def _overrides_view() -> int:
    out = console()
    rules, code = _overrides()
    if code:
        return code

    from iris_ai.toolpolicy import TOOL_DECLARATIONS

    known_classes = {c.value for c in ToolClass}
    table = Table(title="Overrides", title_style="iris.title", header_style="iris.title")
    table.add_column("key")
    table.add_column("applies to")
    table.add_column("policy")
    if not rules:
        out.print("[dim]no overrides set (TOOL_POLICY_OVERRIDES is empty)[/dim]")
    for key, policy in sorted(rules.items()):
        if key in known_classes:
            applies = f"class: {key} (every tool in it)"
        elif key in TOOL_DECLARATIONS:
            applies = f"tool: {key} (core)"
        elif "/" in key:
            applies = f"tool: {key} (external)"
        else:
            applies = "nothing — this key names no tool or class"
        table.add_row(key, applies, _styled(policy.value))
    if rules:
        out.print(table)
    for key in unknown_overrides(settings.tool_policy_overrides):
        out.print(
            f"[iris.warn]unknown override {key!r}[/iris.warn] — it names no tool and no class, "
            "so it changes nothing (and the engine does not fail on it)"
        )
    return 0


def _servers() -> int:
    out = console()
    from iris_ai.mcp import McpConfigError, McpToolInfo, enabled_servers, load_servers, policy_for

    try:
        specs = load_servers()
    except McpConfigError as exc:
        out.print(f"[iris.fail]MCP config is not usable:[/iris.fail] {exc}")
        return 1
    if not specs:
        out.print(
            f"[dim]No MCP servers declared in {settings.mcp_servers_file} "
            "(see config/mcp.json.example).[/dim]"
        )
        return 0

    table = Table(title="Server policy", title_style="iris.title", header_style="iris.title")
    table.add_column("server")
    table.add_column("transport")
    table.add_column("trust")
    table.add_column("approval")
    table.add_column("a read-only tool")
    table.add_column("anything else")
    for spec in load_servers():
        # Two hypothetical tools, because the rule is a function of the
        # declaration: this is the preview of what connecting would produce.
        on = spec.enabled
        read = policy_for(McpToolInfo(name="<read>", read_only=True), spec).policy
        write = policy_for(McpToolInfo(name="<write>"), spec).policy
        table.add_row(
            spec.name if on else f"{spec.name} [dim](off)[/dim]",
            spec.transport,
            spec.trust,
            spec.approval,
            _styled(read.value),
            _styled(write.value),
        )
    out.print(table)
    out.print(
        "[dim]an approval override is checked first and can tighten either row "
        "(`tool_policy_overrides=\"external=deny\"` closes every external tool, or "
        "name one as `server/tool=deny`).[/dim]"
    )
    if not enabled_servers():
        out.print("[dim]every declared server is switched off (`enabled: false`)[/dim]")
    out.print("[dim]for what each server actually offers: `iris plugins mcp --live`[/dim]")
    return 0


def _show() -> int:
    """The default view: classes, then overrides, then servers."""
    for step in (_classes, _overrides_view, _servers):
        code = step()
        if code:
            return code
    return 0


def run(action: str = "show") -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    match action:
        case "show" | "list":
            return _show()
        case "classes":
            return _classes()
        case "overrides":
            return _overrides_view()
        case "servers":
            return _servers()
        case _:
            out.print(f"[iris.fail]unknown action {action!r}[/iris.fail] — use show, classes, overrides or servers")
            return 2
