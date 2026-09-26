"""`iris cron` — the owner's view of time-triggered work.

Three verbs, and the split between them matters:

- `list` — every job with its schedule in words, its next run, its run/miss/
  failure counters, and whether it is currently disabled. Read-only, and it needs
  **no scheduler**: next runs are computed from the stored spec by
  `tasks.next_run_at`, so the CLI works on a machine where the engine is not
  running at all.
- `add` — a job, written to the same store the agent's `schedule_task` tool
  writes, through the same parsers. There is no second grammar for the CLI.
- `rm <id>` — removes one job by id. The two built-ins (nightly sleep, morning
  brief) are not jobs in this store; they are config, so they are not removable
  here and the error says so.

A running engine reads the store at boot. `add`/`rm` therefore take effect on the
next start, or immediately after `POST /cron/reload` on a live `iris-core`.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from rich.table import Table

from iris.cli.help_theme import console
from iris.config import settings
from iris.tasks import (
    CALENDAR,
    INTERVAL,
    Task,
    TaskStore,
    next_run_at,
    parse_calendar,
    parse_every,
    parse_when,
)

BUILTIN_HINT = (
    "the nightly sleep and morning brief are configured in iris/config.py "
    "(nightly_sleep_hour, morning_brief_hour), not stored as jobs"
)


def store_for_cli() -> TaskStore:
    return TaskStore(Path(settings.workspace_dir) / "config" / "tasks.json")


def _when(task: Task, now: datetime) -> str:
    if task.disabled:
        return "disabled"
    nxt = next_run_at(task, now=now)
    if nxt is None:
        return "(unreadable schedule)"
    return nxt.strftime("%Y-%m-%d %H:%M")


def render_list(store: TaskStore) -> int:
    out = console()
    now = datetime.now(ZoneInfo(settings.iris_timezone))
    jobs = store.list()
    if not jobs:
        out.print("[iris.warn]no scheduled jobs[/iris.warn]")
        out.print(f"  {BUILTIN_HINT}")
        return 0
    # Kept narrow on purpose: nine columns wrapped the schedule text on an
    # 80-column terminal, which made the one thing this table is for unreadable.
    # The schedule itself carries the kind ("every 2 hours" vs "at 18:00 daily").
    table = Table(title="Scheduled jobs", title_style="iris.title", header_style="iris.title")
    table.add_column("id")
    table.add_column("schedule")
    table.add_column("next run")
    table.add_column("runs", justify="right")
    table.add_column("miss", justify="right")
    table.add_column("fail", justify="right")
    table.add_column("on", justify="center")
    for task in sorted(jobs, key=lambda t: t.run_at):
        table.add_row(
            task.id,
            task.describe(),
            _when(task, now),
            str(task.runs),
            str(task.missed),
            str(task.failures),
            "no" if task.disabled else "yes",
        )
    out.print(table)
    disabled = [t for t in jobs if t.disabled]
    if disabled:
        out.print(
            f"[iris.warn]{len(disabled)} job(s) disabled after repeated failure[/iris.warn] — "
            "`iris cron rm <id>` then re-add to retry"
        )
    out.print(f"[dim]{BUILTIN_HINT}[/dim]")
    return 0


def render_add(
    store: TaskStore,
    *,
    once: str | None,
    every: str | None,
    at: str | None,
    instruction: str,
) -> int:
    out = console()
    chosen = [name for name, value in (("--once", once), ("--every", every), ("--at", at)) if value]
    if len(chosen) != 1:
        out.print(
            f"[iris.fail]pick exactly one schedule[/iris.fail] — got {', '.join(chosen) or 'none'} "
            "(use --once '<time>', --every '<interval>' or --at 'HH:MM')"
        )
        return 2
    try:
        if once:
            when = parse_when(once)
            if when <= datetime.now(when.tzinfo):
                out.print("[iris.fail]that time has already passed[/iris.fail]")
                return 1
            task = store.add(instruction=instruction, run_at=when)
        elif every:
            delta = parse_every(every)
            now = datetime.now(ZoneInfo(settings.iris_timezone))
            task = store.add(
                instruction=instruction,
                run_at=now + delta,
                kind=INTERVAL,
                every=every.strip(),
                catch_up=settings.cron_interval_catch_up,
            )
        else:
            hour, minute, _days = parse_calendar(at or "")
            now = datetime.now(ZoneInfo(settings.iris_timezone))
            candidate = datetime(now.year, now.month, now.day, hour, minute, tzinfo=now.tzinfo)
            if candidate <= now:
                candidate += timedelta(days=1)
            task = store.add(
                instruction=instruction,
                run_at=candidate,
                kind=CALENDAR,
                at=(at or "").strip(),
                catch_up=True,
            )
    except ValueError as exc:
        out.print(f"[iris.fail]{exc}[/iris.fail]")
        return 1
    out.print(f"[iris.ok]added[/iris.ok] {task.id} — {task.describe()}")
    out.print(f"  instruction: {task.instruction}")
    out.print("[dim]takes effect at the next engine start, or after POST /cron/reload[/dim]")
    return 0


def render_rm(store: TaskStore, job_id: str) -> int:
    out = console()
    if not job_id:
        out.print("[iris.fail]`iris cron rm` needs a job id[/iris.fail] — see `iris cron list`")
        return 2
    if store.get(job_id) is None:
        out.print(f"[iris.fail]no job with id {job_id!r}[/iris.fail]")
        out.print(f"  {BUILTIN_HINT}")
        return 1
    store.remove(job_id)
    out.print(f"[iris.ok]removed[/iris.ok] {job_id}")
    return 0


def run(action: str, job_id: str = "", **kwargs: object) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    store = store_for_cli()
    match action:
        case "list" | "ls":
            return render_list(store)
        case "add":
            return render_add(
                store,
                once=kwargs.get("once") or None,  # type: ignore[arg-type]
                every=kwargs.get("every") or None,  # type: ignore[arg-type]
                at=kwargs.get("at") or None,  # type: ignore[arg-type]
                instruction=str(kwargs.get("instruction") or "").strip(),
            )
        case "rm" | "remove":
            return render_rm(store, job_id)
        case _:
            out.print(f"[iris.fail]unknown action {action!r}[/iris.fail] — use list, add or rm")
            return 2
