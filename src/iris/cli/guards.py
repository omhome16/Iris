"""`iris guards` — the ceilings, on the record.

P8 put a guard chain in front of every tool call. A limit nobody can read is a
limit nobody can trust: this command prints the same snapshot the engine
enforces (`GuardChain.snapshot()`), so "why did Iris refuse that?" has an
answer that does not require reading `traces.jsonl`.

It works with **no engine running**, like `iris cron list`: the policy comes
from settings and the day counters come from `config/budget.json`. What it
cannot show is live *circuit* state, because that is per-run and lives with the
process — the output says so instead of printing a misleading zero.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich import box
from rich.table import Table

from iris.budget import Budget, BudgetPolicy, CounterKind
from iris.cli.help_theme import console
from iris.config import settings
from iris.guards import GuardChain


def budget_path() -> Path:
    return Path(settings.workspace_dir) / "config" / "budget.json"


def snapshot() -> dict:
    """The chain's declared policy plus today's spend, read from disk."""
    budget = Budget(policy=BudgetPolicy.from_settings(), path=budget_path())
    return GuardChain.from_settings(budget).snapshot()


def _ceiling(value: int) -> str:
    return "no ceiling" if not value else f"{value:,}"


def render(data: dict) -> None:
    out = console()
    out.print("[iris.title]Guard chain[/iris.title] — every tool call passes this before it runs")

    # `box.ASCII` on purpose, and the whole output is cp1252-clean: rich
    # downgrades its own borders on a limited console, but it cannot downgrade a
    # character *we* emitted, and a `↑` in this table shipped a traceback instead
    # of a table. Diagnostics get pasted into issues and logs, so portability
    # beats prettier corners.
    chain = Table(show_header=True, header_style="bold", box=box.ASCII)
    chain.add_column("Guard")
    chain.add_column("Ceiling / threshold")
    for name in data["order"]:
        if name == "record":
            chain.add_row("record", "every verdict goes to the turn trace either way")
        elif name == "budget":
            budget = data.get("budget") or {}
            policy = budget.get("policy") or {}
            chain.add_row(
                "budget",
                f"turn {_ceiling(policy.get('max_tokens_per_turn', 0))} · "
                f"day {_ceiling(policy.get('max_tokens_per_day', 0))} tokens",
            )
        elif name == "circuit":
            chain.add_row(
                "circuit",
                f"{data['failure_threshold']} consecutive failures of one tool opens it · "
                f"{data['failing_tools_per_turn']} failing tools escalate the turn",
            )
        elif name == "spiral":
            # No `↑` here: it is not in cp1252, which is what a Windows console
            # falls back to encoding with, and the failure is a traceback rather
            # than a missing glyph.
            chain.add_row(
                "spiral",
                f"same tool {data['spiral_min_repeats']}x with args >="
                f"{data['spiral_jaccard']} similar · {data['max_calls_per_turn']} calls per turn",
            )
        elif name == "context":
            chain.add_row("context", "a refusal carries the reason and what to do instead")
    out.print(chain)
    if not data["enabled"]:
        out.print("[iris.warn]the chain is switched off[/iris.warn] (TOOL_GUARD_ENABLED=false)")

    budget = data.get("budget") or {}
    spend = Table(show_header=True, header_style="bold", title="Today's spend", box=box.ASCII)
    spend.add_column("Kind")
    spend.add_column("Tokens", justify="right")
    counters = budget.get("counters") or {}
    for kind in CounterKind:
        if kind is CounterKind.TOOL_SCHEMA:
            continue  # reserved: no provider reports it separately (see iris/budget.py)
        spend.add_row(kind.value, f"{int(counters.get(kind.value, 0)):,}")
    spend.add_row("[bold]total[/bold]", f"[bold]{int(budget.get('total', 0)):,}[/bold]")
    out.print(spend)
    out.print(
        f"[dim]policy {budget.get('policy', {}).get('version', '?')} · "
        f"day {budget.get('day', '?')} · file {budget_path()}[/dim]"
    )
    if budget.get("persistence_error"):
        out.print(f"[iris.warn]the day counters could not be saved:[/iris.warn] {budget['persistence_error']}")
    out.print(
        "[dim]circuit state is per-run: `GET /guards` on a running engine shows which tools are open.[/dim]"
    )


def run(*, json_output: bool = False) -> int:
    data = snapshot()
    if json_output:
        print(json.dumps(data, indent=2, ensure_ascii=False))
        return 0
    render(data)
    return 0
