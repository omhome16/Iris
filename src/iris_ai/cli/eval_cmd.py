"""`iris eval` — score the active harness, or one component, offline."""

from __future__ import annotations

from iris_ai.cli.help_theme import console
from iris_ai.eval.score import PRIMARY, archive_run, compare_suite, list_suites, run_suite


def _line(row: dict) -> str:
    kind = str(row.get("kind") or "")
    metric = PRIMARY.get(kind, ("stale_as_current", "decrease"))[0]
    value = row.get(metric)
    shown = f"{value:.2f}" if isinstance(value, float) else "-"
    return f"{kind}/{row['suite']}  {row['component']}  {metric}={shown}"


def run(
    kind: str = "",
    *,
    suite: str = "",
    component: str = "",
    against: str = "",
    split: str = "",
    live: bool = False,
    json_output: bool = False,
    archive: bool = True,
) -> int:
    from iris_ai.eval.score import refuse_live

    refuse_live(live, approved=True)
    out = console()
    if live and kind == "engine":
        from iris_ai.eval.score import run_engine_live

        report = run_engine_live()
        out.print("engine --live  scripted tools")
        for row in report["rows"]:
            out.print(
                f"{row['id']}  {row['component']}  success={row['success']:.2f}  "
                f"tool_calls={row['tool_calls']:.0f}  tokens={row['tokens']:.0f}  "
                f"latency_ms={row['latency_ms']:.1f}"
            )
        compared = report["comparison"]
        low, high = compared["ci"]
        out.print(
            f"tasks  plan-execute vs react  {compared['metric']} "
            f"{compared['baseline']:.2f} -> {compared['candidate']:.2f}  "
            f"CI [{low:+.2f}, {high:+.2f}]  {compared['verdict']}"
        )
        for label in ("tool_calls", "tokens", "latency_ms"):
            side = compared[label]
            out.print(f"  {label}  react={side['react']:.1f}  plan-execute={side['plan-execute']:.1f}")
        return 0
    paths = list_suites(kind)
    if suite:
        paths = [path for path in paths if path.stem == suite or path.name == suite]
    out = console()
    if not paths:
        out.print("no suites")
        return 1
    chosen = component or "default"
    if against or (component and component != "default"):
        rows = [
            compare_suite(path, chosen, against=against or "default", split=split) for path in paths
        ]
    else:
        rows = [run_suite(path, component=chosen, split=split) for path in paths]
    if archive and rows:
        from pathlib import Path

        from iris_ai.config import settings

        archive_run(Path(settings.workspace_dir), {"rows": rows, "component": chosen})
    if json_output:
        import json

        out.print(json.dumps(rows, indent=2))
        return 0
    for row in rows:
        if "ci" in row:
            low, high = row["ci"]
            out.print(
                f"{row['kind']}/{row['suite']}  {row['component']} vs {row['against']}  "
                f"{row['metric']} {row['baseline']:.2f} -> {row['candidate']:.2f}  "
                f"CI [{low:+.2f}, {high:+.2f}]  {row['verdict']}"
            )
        else:
            out.print(_line(row))
    return 0
