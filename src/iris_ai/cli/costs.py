"""`iris costs` — what the model calls actually cost, from the ledger.

The API has `/costs`; this is the same rollup for someone at a terminal, and it
exists because "how much did last week cost" should not require the service to be
running. The ledger is the append-only `config/llm_calls.jsonl` the `LLMClient`
already writes, so this reads what was recorded rather than estimating anything.

Two honesty rules carried from the ledger itself:

- **A model with no price is named, not estimated at zero.** `unpriced_models`
  reports them, so a total is never quietly *lower* than reality.
- **An empty ledger says so**, rather than printing a confident `$0.00`.
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.ledger import CostLedger


def run(action: str = "summary", days: int = 14) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    path = Path(settings.workspace_dir) / "config" / "llm_calls.jsonl"
    ledger = CostLedger(path)
    rows = ledger.rows()
    if not rows:
        out.print(f"[dim]No model calls recorded yet ({path} is empty or absent).[/dim]")
        out.print("[dim]the ledger fills as Iris runs; `iris chat` is the quickest way.[/dim]")
        return 0

    if action in ("summary", "totals"):
        return _summary(out, ledger, rows)
    if action in ("daily", "days"):
        return _daily(out, ledger, days)
    if action in ("weekly", "weeks"):
        return _weekly(out, ledger)
    out.print(f"[iris.fail]unknown action {action!r}[/iris.fail] — use summary, daily or weekly")
    return 2


def _unpriced_note(out, totals: dict) -> None:
    """A total is only a total if every model in it was priced."""
    missing = totals.get("unpriced_models") or []
    if missing:
        out.print(
            f"[iris.warn]{len(missing)} model(s) have no price in the table, so this is a "
            f"lower bound, not the total:[/iris.warn] {', '.join(missing)}"
        )
        out.print("[dim]model_costs.py holds the price table; an unpriced model records 0.0.[/dim]")


def _summary(out, ledger: CostLedger, rows: list[dict]) -> int:
    totals = ledger.totals()
    out.print(
        f"[iris.title]Spend[/iris.title] — {totals.get('requests', 0)} calls, "
        f"${totals.get('cost', 0.0):.4f}"
    )
    out.print(
        f"[dim]{totals.get('prompt_tokens', 0):,} prompt tokens "
        f"({totals.get('cached_tokens', 0):,} cached), "
        f"{totals.get('completion_tokens', 0):,} completion tokens[/dim]"
    )
    _unpriced_note(out, totals)

    by_model: dict[str, dict] = {}
    for row in rows:
        key = str(row.get("model", "unknown"))
        bucket = by_model.setdefault(key, {"calls": 0, "cost": 0.0})
        bucket["calls"] += 1
        bucket["cost"] += float(row.get("cost") or 0.0)
    table = Table(title="By model", title_style="iris.title", header_style="iris.title")
    table.add_column("model")
    table.add_column("calls", justify="right")
    table.add_column("cost", justify="right")
    for model, bucket in sorted(by_model.items(), key=lambda kv: -kv[1]["cost"]):
        table.add_row(model, str(bucket["calls"]), f"${bucket['cost']:.4f}")
    out.print(table)
    return 0


def _daily(out, ledger: CostLedger, days: int) -> int:
    table = Table(title=f"Last {days} days", title_style="iris.title", header_style="iris.title")
    table.add_column("day")
    table.add_column("calls", justify="right")
    table.add_column("prompt", justify="right")
    table.add_column("completion", justify="right")
    table.add_column("cost", justify="right")
    for row in ledger.daily_totals(days=max(1, days)):
        table.add_row(
            str(row.get("day", "")),
            str(row.get("requests", 0)),
            f"{row.get('prompt_tokens', 0):,}",
            f"{row.get('completion_tokens', 0):,}",
            f"${float(row.get('cost') or 0.0):.4f}",
        )
    out.print(table)
    return 0


def _weekly(out, ledger: CostLedger) -> int:
    table = Table(title="Last 4 weeks", title_style="iris.title", header_style="iris.title")
    table.add_column("week")
    table.add_column("calls", justify="right")
    table.add_column("cost", justify="right")
    for row in ledger.weekly_totals():
        table.add_row(
            str(row.get("week", "")),
            str(row.get("requests", 0)),
            f"${float(row.get('cost') or 0.0):.4f}",
        )
    out.print(table)
    return 0
