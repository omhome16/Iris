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

from iris_ai.agents.roles import ROLES, RoleError, get_role
from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.trace import TraceLogger

# Which turn-log events are worth surfacing as "a decision was made".
_DECISION_EVENTS = ("handoff", "agent_effort", "answer_check", "merge")


def render_roles() -> int:
    out = console()
    ui.header(out, "Agent roles", "who may be delegated to, and the bound that shapes each one")
    table = ui.table(
        out,
        "Declared roles",
        [
            "role",
            "tier",
            "lane",
            "tools",
            ("rounds", {"justify": "right"}),
            ("chars", {"justify": "right"}),
        ],
    )
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
    out.print()
    ui.note(
        out,
        "the lead is the main agent: it orchestrates, executes and authors. "
        "There is no executor role on purpose.",
    )
    if not settings.multi_agent_enabled:
        ui.warn(out, "multi_agent_enabled is false — delegation is refused")
    return 0


def render_show(name: str) -> int:
    out = console()
    try:
        role = get_role(name)
    except RoleError as exc:
        ui.error(out, str(exc))
        return 1
    ui.header(out, role.name, f"{role.tier} tier - {role.description}")
    ui.grid(
        out,
        [
            ("recall lane", role.search_lane),
            ("tools", ", ".join(sorted(role.tools)) or "(none)"),
            ("tool rounds", str(role.max_tool_rounds)),
            ("output cap", f"{role.max_output_chars} chars"),
            ("run by", "iris_ai.agents.runner.RoleRunner"),
        ],
    )
    ui.section(out, "system prompt")
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
        ui.warn(out, "no agent decisions in the recent traces")
        ui.note(out, "delegation is opt-in: a turn that never delegates records nothing")
        return 0
    ui.header(out, "Agent decisions", "recent delegations, read back out of the turn traces")
    table = ui.table(
        out,
        "",
        [
            "event",
            "from",
            "to",
            "claims",
            ("unsourced", {"justify": "right"}),
            ("tokens", {"justify": "right"}),
            ("ms", {"justify": "right"}),
        ],
    )
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
                ui.error(out, "`iris agents show` needs a role name")
                ui.note(out, f"roles: {', '.join(sorted(ROLES))}")
                return 2
            return render_show(name)
        case _:
            ui.failed(out, f"unknown action {action!r}", "- use roles, show or handoffs")
            return 2
