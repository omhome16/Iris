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

from iris_ai.cli import ui
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
        ui.failed(out, "tool_policy_overrides is invalid:", str(exc))
        ui.note(out, "format: name or class = allow | ask | deny, comma-separated")
        return {}, 1


def _classes(*, heading: bool = True) -> int:
    out = console()
    if heading:
        ui.header(out, "Capability classes", "what each class of tool defaults to, and whether an override moved it")
    else:
        ui.section(out, "Capability classes")
    rules, code = _overrides()
    if code:
        return code

    from iris_ai.toolpolicy import EXTERNAL_TOOLS, TOOL_DECLARATIONS

    counts: Counter[str] = Counter(
        [d.cls.value for d in TOOL_DECLARATIONS.values()]
        + [t.cls.value for t in EXTERNAL_TOOLS.values()]
    )
    table = ui.table(out, "", ["class", "default", "override", "tools"])
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
    out.print()
    ui.note(
        out,
        "precedence: class default < a tool's own source (an MCP server's trust) "
        "< a class override < a per-tool override. `deny` wins outright at every level, "
        "so an override can tighten and never loosen.",
    )
    return 0


def _overrides_view(*, heading: bool = True) -> int:
    out = console()
    if heading:
        ui.header(out, "Overrides", "every key in TOOL_POLICY_OVERRIDES, and what it actually applies to")
    else:
        ui.section(out, "Overrides")
    rules, code = _overrides()
    if code:
        return code

    from iris_ai.toolpolicy import TOOL_DECLARATIONS

    known_classes = {c.value for c in ToolClass}
    table = ui.table(out, "", ["key", "applies to", "policy"])
    if not rules:
        ui.note(out, "no overrides set (TOOL_POLICY_OVERRIDES is empty)")
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
        ui.warn(
            out,
            f"unknown override {key!r} — it names no tool and no class, "
            "so it changes nothing (and the engine does not fail on it)",
        )
    return 0


def _servers(*, heading: bool = True) -> int:
    out = console()
    from iris_ai.mcp import McpConfigError, McpToolInfo, enabled_servers, load_servers, policy_for

    try:
        specs = load_servers()
    except McpConfigError as exc:
        ui.failed(out, "MCP config is not usable:", str(exc))
        return 1
    if not specs:
        ui.note(
            out,
            f"No MCP servers declared in {settings.mcp_servers_file} "
            "(see config/mcp.json.example).",
        )
        return 0

    if heading:
        ui.header(out, "Server policy", "what a tool from each declared server would be allowed to do")
    else:
        ui.section(out, "Server policy")
    table = ui.table(
        out,
        "",
        ["server", "transport", "trust", "approval", "a read-only tool", "anything else"],
    )
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
    out.print()
    ui.note(
        out,
        "an approval override is checked first and can tighten either row "
        "(`tool_policy_overrides=\"external=deny\"` closes every external tool, or "
        "name one as `server/tool=deny`).",
    )
    if not enabled_servers():
        ui.note(out, "every declared server is switched off (`enabled: false`)")
    ui.note(out, "for what each server actually offers: `iris plugins mcp --live`")
    return 0


def _show() -> int:
    """The default view: classes, then overrides, then servers, under one header."""
    out = console()
    ui.header(out, "Policy", "what every tool and server is allowed to do, and where each decision came from")
    for step in (_classes, _overrides_view, _servers):
        code = step(heading=False)
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
            ui.failed(out, f"unknown action {action!r}", "- use show, classes, overrides or servers")
            return 2
