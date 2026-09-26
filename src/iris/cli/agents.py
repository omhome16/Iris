"""`iris agents` — read-only inspection of the multi-agent layer.

The blueprint's promise for P5 is "which agent decided what", and that promise
is only kept if the owner can look. Three verbs, all observation:

- `roles` — every declared role with the bound that shapes it: tier, recall
  lane, tool allowlist, tool-round cap and output cap.
- `show <name>` — one role's full system prompt and bounds.
- `handoffs` — recent delegations read back out of the turn traces: who asked
  whom, how many claims came back, how many were unsourced, and what it cost in
  tokens and milliseconds.

Nothing here writes, and nothing here runs an agent: a role is data, so the CLI
reads the same declarations the runner enforces.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from iris.agents.roles import ROLES, RoleError, get_role
from iris.cli.help_theme import console
from iris.config import settings
from iris.trace import TraceLogger

# Which turn-log events are worth surfacing as "a decision was made".
_DECISION_EVENTS = ("handoff", "agent_effort", "answer_check", "merge")


def render_roles() -> int:
    out = console()
    table = Table(title="Agent roles", title_style="iris.title", header_style="iris.title")
    table.add_column("role")
    table.add_column("tier")
    table.add_column("lane")
    table.add_column("tools")
    table.add_column("rounds", justify="right")
    table.add_column("chars", justify="right")
    for role in ROLES.values():
        table.add_row(
            role.name,
            role.tier,
            role.search_lane,
            ", ".join(sorted(role.tools)) or "(none)",
            str(role.max_tool_rounds),
            str(role.max_output_chars),
        )
    out.print(table)
    out.print(
        "[dim]the lead is the main agent: it orchestrates, executes and authors. "
        "There is no executor role on purpose.[/dim]"
    )
    if not settings.multi_agent_enabled:
        out.print("[iris.warn]multi_agent_enabled is false — delegation is refused[/iris.warn]")
    return 0


def render_show(name: str) -> int:
    out = console()
    try:
        role = get_role(name)
    except RoleError as exc:
        out.print(f"[iris.fail]{exc}[/iris.fail]")
        return 1
    out.print(f"[iris.title]{role.name}[/iris.title] [dim]({role.tier} tier)[/dim]")
    out.print(f"  description: {role.description}")
    for label, value in (
        ("recall lane", role.search_lane),
        ("tools", ", ".join(sorted(role.tools)) or "(none)"),
        ("tool rounds", str(role.max_tool_rounds)),
        ("output cap", f"{role.max_output_chars} chars"),
        ("run by", "iris.agents.runner.RoleRunner"),
    ):
        out.print(f"  {label}: {value}")
    out.print("")
    out.print(role.system_prompt)
    return 0


def handoffs_from_traces(limit: int = 10) -> list[tuple[str, dict]]:
    """Recent decision events, newest first, flattened out of the traces.

    Traces are the store `GET /traces` already serves; reading them here means
    the CLI reports exactly what the running system recorded, rather than a
    second bookkeeping path that could disagree with it.
    """
    logger = TraceLogger(Path(settings.workspace_dir) / "config" / "traces.jsonl")
    found: list[tuple[str, dict]] = []
    for trace in logger.recent(max(1, limit * 4)):
        judgment = trace.get("judgment") or {}
        for event in judgment.get("events") or []:
            if event.get("kind") in _DECISION_EVENTS:
                entry = dict(event)
                entry["session"] = trace.get("session_id") or trace.get("session") or ""
                found.append((str(event.get("kind")), entry))
            if len(found) >= limit:
                return found
    return found


def render_handoffs(limit: int = 10) -> int:
    out = console()
    rows = handoffs_from_traces(limit)
    if not rows:
        out.print("[iris.warn]no agent decisions in the recent traces[/iris.warn]")
        out.print("  delegation is opt-in: a turn that never delegates records nothing")
        return 0
    table = Table(title="Agent decisions", title_style="iris.title", header_style="iris.title")
    table.add_column("event")
    table.add_column("from")
    table.add_column("to")
    table.add_column("claims")
    table.add_column("unsourced", justify="right")
    table.add_column("tokens", justify="right")
    table.add_column("ms", justify="right")
    for kind, event in rows:
        table.add_row(
            kind,
            str(event.get("from") or event.get("handoff_kind") or "-"),
            str(event.get("to") or event.get("verdict") or "-"),
            str(event.get("claims", event.get("multi_part", "-"))),
            str(event.get("unsourced", "-")),
            str(event.get("tokens", "-")),
            str(event.get("ms", "-")),
        )
    out.print(table)
    return 0


def run(action: str, name: str | None = None, limit: int = 10) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    match action:
        case "roles" | "list":
            return render_roles()
        case "handoffs":
            return render_handoffs(limit)
        case "show":
            if not name:
                out.print("[iris.fail]`iris agents show` needs a role name[/iris.fail]")
                out.print(f"  roles: {', '.join(sorted(ROLES))}")
                return 2
            return render_show(name)
        case _:
            out.print(f"[iris.fail]unknown action {action!r}[/iris.fail] — use roles, show or handoffs")
            return 2
