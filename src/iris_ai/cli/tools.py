"""`iris tools` — read-only inspection of the tool surface and its policy.

P7 replaced two hand-maintained allowlists with declarations, and a declaration
nobody can read is only half a fix. Two verbs, both observation:

- `policy` (default) — every declared tool with its class, the policy that class
  resolves to, where that decision came from (class default vs an override),
  which namespace it belongs to, and whether it is on the always-visible surface
  or deferrable. It also prints the catalog the prompt gets for the deferred
  remainder, so "what does the model know exists" is readable rather than
  inferred from a schema count.
- `actions` — recent computer-use actions read back out of
  `config/actions.jsonl`: what was attempted, where, whether it was allowed, and
  never what was typed.

Nothing here writes, and nothing here needs an engine: the policy is data and the
audit log is a file.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.computer.audit import ActionLog
from iris_ai.config import settings
from iris_ai.toolpolicy import (
    NAMESPACE_PURPOSE,
    TOOL_DECLARATIONS,
    PolicyError,
    deferred_catalog,
    namespace_of,
    policy_snapshot,
    surface_order,
    unknown_overrides,
)

_POLICY_STYLE = {"allow": "iris.ok", "ask": "iris.warn", "deny": "iris.fail"}


def render_policy() -> int:
    out = console()
    ui.header(out, "Tool surface", "every declared tool, its policy, and where that decision came from")
    overrides = settings.tool_policy_overrides
    try:
        rows = policy_snapshot(overrides)
    except PolicyError as exc:
        # A malformed override stops the engine booting too; the CLI says why
        # instead of raising a traceback over a typo in `.env`.
        ui.failed(out, "tool_policy_overrides is invalid:", str(exc))
        ui.note(out, "format: name or class = allow | ask | deny, comma-separated")
        return 1
    # A tool that is not registered this boot cannot be shown, deferred or
    # otherwise: `computer` with computer-use off is *absent*, and calling it
    # "visible" would describe a capability the surface does not have.
    present = None if settings.computer_enabled else [n for n in TOOL_DECLARATIONS if n != "computer"]
    visible, deferred = surface_order(settings.tool_surface_budget, present=present)
    visible_set, deferred_set = set(visible), set(deferred)

    table = ui.table(
        out,
        "Declared tools",
        [("tool", {"no_wrap": True, "overflow": "fold"}), "class", "namespace", "policy", "source", "surface"],
    )
    for row in rows:
        style = _POLICY_STYLE.get(row["policy"], "")
        if row["tool"] in deferred_set:
            surface = "deferred"
        elif row["tool"] in visible_set:
            surface = "visible"
        else:
            surface = "off"
        table.add_row(
            row["tool"],
            row["class"],
            namespace_of(row["tool"]) or "-",
            f"[{style}]{row['policy']}[/{style}]" if style else row["policy"],
            row["source"],
            surface,
        )
    out.print(table)
    out.print()
    ui.note(
        out,
        f"visible budget {settings.tool_surface_budget}: "
        f"{len(visible)} shown, {len(deferred)} deferred. "
        f"Deferral is presentation, not permission — a deferred tool is still callable, "
        f"`find_tools` loads a schema (appended at the tail, so the cached prefix "
        f"survives), and a connected channel's own tools are promoted at runtime.",
    )
    catalog = deferred_catalog(deferred)
    if catalog:
        # The verbatim prompt section, not a summary of it: the question this
        # readout exists to answer is what the model was actually told, and a
        # paraphrase cannot answer that.
        ui.section(out, "the prompt sees, for the deferred remainder")
        for line in catalog.splitlines():
            ui.note(out, line)
    missing = [n for n in TOOL_DECLARATIONS if namespace_of(n) not in NAMESPACE_PURPOSE]
    if missing:
        ui.warn(
            out,
            f"{len(missing)} tool(s) in a namespace with no purpose line — "
            "they still appear in the catalog, without a description",
        )
    for key in unknown_overrides(overrides):
        ui.warn(out, f"unknown tool_policy_override {key!r} — names no tool or class")
    if not settings.computer_enabled:
        ui.note(out, "computer-use is off (`computer_enabled=false`) — the `computer` tool is not registered")
    return 0


def render_actions(limit: int = 20) -> int:
    out = console()
    ui.header(out, "Computer actions", "what was attempted, where, and whether it was allowed - never what was typed")
    rows = ActionLog(Path(settings.workspace_dir) / "config" / "actions.jsonl").recent(max(1, limit))
    if not rows:
        ui.warn(out, "no computer actions recorded")
        ui.note(out, "nothing has tried to drive a screen, or computer-use is off")
        return 0
    table = ui.table(
        out,
        "Recent actions",
        ["time", "action", "target", "decision", "ok"],
    )
    for row in rows:
        table.add_row(
            str(row.get("ts", ""))[:19],
            str(row.get("action", "")),
            str(row.get("url") or row.get("target") or "-")[:60],
            str(row.get("decision", "")),
            "yes" if row.get("ok") else "no",
        )
    out.print(table)
    return 0


def run(action: str = "policy", limit: int = 20) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    match action:
        case "policy" | "list":
            return render_policy()
        case "actions":
            return render_actions(limit)
        case _:
            out.print(f"[iris.fail]unknown action {action!r}[/iris.fail] — use policy or actions")
            return 2
