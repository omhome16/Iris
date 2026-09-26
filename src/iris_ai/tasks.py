"""Scheduled tasks — future actions Iris commits to, once or repeatedly.

`schedule_task` (agent tool) parses a time and persists a task to
`workspace/config/tasks.json` (files are the source of truth). At boot the
TaskScheduler re-registers every pending task as an APScheduler job. When a job
fires, the instruction runs through the chat graph exactly as if the owner had
sent it, the reply is delivered over Telegram when the channel and owner chat
are known, and a one-off task is removed while a recurring job stays.

`when` formats (timezone = settings.iris_timezone):
  - ISO 8601:           2026-08-22T09:00  (with or without offset)
  - relative:           in 3 days · in 90 minutes · in 2 weeks
  - shorthand:          tomorrow · tomorrow 9:30 · today 21:30 · 9:30

Three schedule kinds share this one store and one run path (P6):

| kind | spec | trigger |
|---|---|---|
| `once` | `run_at` | `DateTrigger` — the shipped behaviour, unchanged |
| `interval` | `every` (`"15 minutes"`) | `IntervalTrigger` |
| `calendar` | `at` (`"09:30"`) + optional `weekdays` | `CronTrigger` |

**Misfire policy is explicit, not inherited.** The vault's rule (Agent Failure
Engineering, Agent Control) is that bounds are declared and tested rather than
implicit, and this file used to have one implicit bound — `misfire_grace_time`
wired into two hardcoded jobs — plus a `register_all()` that silently dropped any
one-off whose time passed while the process was down. `missed_decision()` is now
the single place that answers "the window passed, now what?", and every answer is
recorded on the job (`missed`, `runs`, `last_outcome`) so "why didn't my reminder
fire?" is answerable from the store.

A recurring job that keeps failing is **disabled after
`settings.cron_max_failures` consecutive failures** — the circuit-breaker
principle applied to time, so a permanently broken job stops costing a graph run
per interval forever.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from iris_ai.config import settings

log = logging.getLogger("iris_ai.tasks")

_RELATIVE_RE = re.compile(
    r"^in (\d+) (minute|minutes|hour|hours|day|days|week|weeks)$", re.IGNORECASE
)
_TIME_RE = re.compile(r"^(today|tomorrow)?\s*(\d{1,2}):(\d{2})$", re.IGNORECASE)
_DAY_ONLY_RE = re.compile(r"^(today|tomorrow)$", re.IGNORECASE)
_INTERVAL_RE = re.compile(
    r"^(?:every )?(\d+) (minute|minutes|hour|hours|day|days)$", re.IGNORECASE
)
_AT_RE = re.compile(r"^(\d{1,2}):(\d{2})$")

DEFAULT_REMINDER_HOUR = 9  # bare "tomorrow" / "today" default time

# Schedule kinds. `once` is the shipped one; the other two are P6.
ONCE, INTERVAL, CALENDAR = "once", "interval", "calendar"
KINDS = (ONCE, INTERVAL, CALENDAR)

# Misfire outcomes.
RUN_NOW, SKIP, EXPIRE = "run_now", "skip", "expire"

_WEEKDAY_NAMES = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
}


def parse_when(when: str) -> datetime:
    """Parse an owner/agent-provided time into an aware datetime.
    Raises ValueError for anything unparseable."""
    tz = ZoneInfo(settings.iris_timezone)
    now = datetime.now(tz)
    text = when.strip()
    if not text:
        raise ValueError("no time given")

    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=tz)  # naive → owner's timezone
        else:
            parsed = parsed.astimezone(tz)
        return parsed
    except ValueError:
        pass

    m = _RELATIVE_RE.match(text)
    if m:
        amount = int(m.group(1))
        unit = m.group(2).rstrip("s")
        delta = {
            "minute": timedelta(minutes=amount),
            "hour": timedelta(hours=amount),
            "day": timedelta(days=amount),
            "week": timedelta(weeks=amount),
        }[unit]
        return now + delta

    m = _TIME_RE.match(text)
    if m:
        day_part = (m.group(1) or "today").casefold()
        hour, minute = int(m.group(2)), int(m.group(3))
        if hour > 23 or minute > 59:
            raise ValueError(f"invalid time: {text}")
        base = now.date()
        if day_part == "tomorrow":
            base = now.date() + timedelta(days=1)
        candidate = datetime(base.year, base.month, base.day, hour, minute, tzinfo=tz)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    # Bare "tomorrow" / "today" → the documented reminder hour (09:00).
    # This previously used `nightly_sleep_hour` for "tomorrow", which silently
    # scheduled a bare "tomorrow" reminder at the owner's 04:00 dream hour.
    m = _DAY_ONLY_RE.match(text)
    if m:
        day_part = m.group(1).casefold()
        base = now.date() + (timedelta(days=1) if day_part == "tomorrow" else timedelta(0))
        candidate = datetime(base.year, base.month, base.day, DEFAULT_REMINDER_HOUR, 0, tzinfo=tz)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    raise ValueError(f"could not understand when: {when!r} (try ISO '2026-08-22T09:00' or 'in 3 days')")


def parse_every(every: str) -> timedelta:
    """Parse a recurring interval: `"15 minutes"`, `"every 2 hours"`, `"3 days"`.

    Deliberately narrow — minutes, hours, days. A seconds-granularity job would
    let a typo turn into a graph run every second, and "every week" is better
expressed as a calendar job with `weekdays`.
    """
    m = _INTERVAL_RE.match((every or "").strip())
    if not m:
        raise ValueError(
            f"could not understand interval: {every!r} (try '15 minutes', '2 hours', '3 days')"
        )
    amount = int(m.group(1))
    if amount < 1:
        raise ValueError("an interval must be at least 1")
    unit = m.group(2).rstrip("s")
    if unit == "minute":
        return timedelta(minutes=amount)
    if unit == "hour":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def parse_calendar(at: str, weekdays: str = "") -> tuple[int, int, str]:
    """Parse a calendar job's time and optional weekday list.

    Returns `(hour, minute, cron_day_of_week)`. Monday is 0, matching APScheduler
    and cron's `mon-sun` naming; an empty `weekdays` means every day.
    """
    m = _AT_RE.match((at or "").strip())
    if not m:
        raise ValueError(f"could not understand time of day: {at!r} (try '09:30')")
    hour, minute = int(m.group(1)), int(m.group(2))
    if hour > 23 or minute > 59:
        raise ValueError(f"invalid time of day: {at!r}")

    days = [d.strip().casefold()[:3] for d in (weekdays or "").split(",") if d.strip()]
    if not days:
        return hour, minute, "*"
    unknown = [d for d in days if d not in _WEEKDAY_NAMES]
    if unknown:
        raise ValueError(f"unknown weekday(s): {', '.join(unknown)} (use mon,tue,wed,thu,fri,sat,sun)")
    names = sorted({_WEEKDAY_NAMES[d] for d in days})
    return hour, minute, ",".join(str(n) for n in names)


def missed_decision(
    *,
    kind: str,
    catch_up: bool,
    last_run: str,
    interval_seconds: int,
    now: datetime,
    grace_seconds: int,
) -> str:
    """What to do about a window that passed while the process was down.

    The single place this question is answered, so the policy is one testable
    function instead of an inherited `misfire_grace_time=` on two jobs.

    - `once`: past its time and outside the grace window → `EXPIRE` (recorded,
      not silently dropped). Inside the grace window it still runs.
    - `interval`: `catch_up` runs it once, late — never once per missed window,
      because a 15-minute job that missed 40 runs must not fire 40 times.
    - `calendar`: defaults to catching up, since these are the owner's daily
      anchors (a morning brief an hour late is useful; one 12 hours late is not).
    """
    late_by = None
    if last_run:
        try:
            late_by = (now - datetime.fromisoformat(last_run)).total_seconds()
        except ValueError:
            late_by = None
    if late_by is None:
        # Never run, or an unreadable timestamp: treat as a normal first run.
        return RUN_NOW
    if late_by <= grace_seconds:
        return RUN_NOW
    if kind == ONCE:
        return EXPIRE
    if kind == INTERVAL and interval_seconds and late_by > interval_seconds * 3:
        return RUN_NOW if catch_up else SKIP
    return RUN_NOW if catch_up else SKIP


def next_run_at(task: Task, *, now: datetime) -> datetime | None:
    """When this job fires next, computed from its own spec.

    A pure function on purpose: the CLI has to answer "when does this run?"
    without a scheduler, a broker or a live engine, and the API has to answer it
    for a job whose trigger object belongs to a different code path. Returns
    `None` for a disabled job or an unreadable row rather than guessing.
    """
    if task.disabled:
        return None
    try:
        if task.kind == INTERVAL:
            delta = parse_every(task.every)
            base = datetime.fromisoformat(task.last_run or task.run_at)
            nxt = base + delta
            # A job that missed many windows still has exactly one next run.
            if nxt <= now:
                behind = (now - nxt) // delta + 1
                nxt += delta * behind
            return nxt
        if task.kind == CALENDAR:
            hour, minute, days = parse_calendar(task.at, task.weekdays)
            wanted = None if days == "*" else {int(d) for d in days.split(",")}
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            if wanted is not None:
                for _ in range(8):  # a week is enough to find any allowed weekday
                    if candidate.weekday() in wanted:
                        break
                    candidate += timedelta(days=1)
            return candidate
        return datetime.fromisoformat(task.run_at)
    except (ValueError, KeyError, ZeroDivisionError):
        return None


@dataclass(slots=True)
class Task:
    id: str
    run_at: str  # ISO 8601, aware (one-off) or the first run (recurring)
    instruction: str
    session_id: str = "default"
    created: str = ""
    # ── P6 ────────────────────────────────────────────────────────────────
    kind: str = ONCE
    every: str = ""  # interval kind
    at: str = ""  # calendar kind, "HH:MM"
    weekdays: str = ""  # calendar kind, "mon,tue"
    catch_up: bool = False
    # Observability so "why didn't my reminder fire?" is answerable.
    runs: int = 0
    missed: int = 0
    failures: int = 0
    last_run: str = ""
    last_outcome: str = ""
    disabled: bool = False

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def recurring(self) -> bool:
        return self.kind in (INTERVAL, CALENDAR)

    def describe(self) -> str:
        """Human-readable schedule, for the CLI and `/tasks`."""
        if self.kind == INTERVAL:
            return f"every {self.every}"
        if self.kind == CALENDAR:
            return f"at {self.at}" + (f" on {self.weekdays}" if self.weekdays else " daily")
        return f"once at {self.run_at[:16].replace('T', ' ')}"


class TaskStore:
    """JSON persistence for one-off tasks (workspace/config/tasks.json)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _load(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return list(data.get("tasks", []))
        except (json.JSONDecodeError, TypeError):
            return []

    def _save(self, tasks: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"tasks": tasks}, indent=2), encoding="utf-8", newline="\n"
        )

    @staticmethod
    def _to_task(t: dict) -> Task:
        """Parse a stored row. Every P6 field is defaulted, so a tasks.json
        written before P6 loads unchanged as a `once` job."""
        kind = str(t.get("kind") or ONCE)
        return Task(
            id=str(t.get("id")),
            run_at=str(t.get("run_at", "")),
            instruction=str(t.get("instruction")),
            session_id=str(t.get("session_id", "default")),
            created=str(t.get("created", "")),
            kind=kind if kind in KINDS else ONCE,
            every=str(t.get("every", "")),
            at=str(t.get("at", "")),
            weekdays=str(t.get("weekdays", "")),
            catch_up=bool(t.get("catch_up", False)),
            runs=int(t.get("runs", 0) or 0),
            missed=int(t.get("missed", 0) or 0),
            failures=int(t.get("failures", 0) or 0),
            last_run=str(t.get("last_run", "")),
            last_outcome=str(t.get("last_outcome", "")),
            disabled=bool(t.get("disabled", False)),
        )

    def list(self) -> list[Task]:
        return [self._to_task(t) for t in self._load()]

    def count(self) -> int:
        return len(self._load())

    def get(self, task_id: str) -> Task | None:
        return next((t for t in self.list() if t.id == task_id), None)

    def add(
        self,
        *,
        instruction: str,
        run_at: datetime,
        session_id: str = "default",
        kind: str = ONCE,
        every: str = "",
        at: str = "",
        weekdays: str = "",
        catch_up: bool = False,
    ) -> Task:
        if kind not in KINDS:
            raise ValueError(f"unknown schedule kind {kind!r}")
        if self.count() >= settings.cron_max_jobs:
            raise ValueError(
                f"too many scheduled jobs ({settings.cron_max_jobs} max) — remove one first"
            )
        now = datetime.now(ZoneInfo(settings.iris_timezone))
        task = Task(
            id=uuid.uuid4().hex[:12],
            run_at=run_at.isoformat(),
            instruction=instruction,
            session_id=session_id,
            created=now.isoformat(timespec="seconds"),
            kind=kind,
            every=every,
            at=at,
            weekdays=weekdays,
            catch_up=catch_up,
        )
        tasks = self._load()
        tasks.append(task.to_dict())
        self._save(tasks)
        return task

    def update(self, task_id: str, **fields: object) -> Task | None:
        """Patch one stored row. Used for run/miss/failure bookkeeping."""
        tasks = self._load()
        updated: Task | None = None
        for t in tasks:
            if t.get("id") == task_id:
                t.update(fields)
                updated = self._to_task(t)
                break
        if updated is not None:
            self._save(tasks)
        return updated

    def remove(self, task_id: str) -> bool:
        tasks = [t for t in self._load() if t.get("id") != task_id]
        if len(tasks) == len(self._load()):
            return False
        self._save(tasks)
        return True


class TaskScheduler:
    """Wires persisted tasks into APScheduler one-off jobs and back."""

    def __init__(self, store: TaskStore, runtime, graph, scheduler: AsyncIOScheduler) -> None:
        self.store = store
        self.runtime = runtime
        self.graph = graph
        self.scheduler = scheduler

    def register_all(self) -> None:
        """Re-register every stored job, applying the misfire policy.

        A one-off whose window passed is **recorded as missed and dropped** — the
        behaviour used to be a silent delete, which made "my reminder never
        arrived" unanswerable. A recurring job is always re-registered, with its
        missed window counted and the run/skip decision taken by
        `missed_decision()`.
        """
        tz = ZoneInfo(settings.iris_timezone)
        now = datetime.now(tz)
        for task in self.store.list():
            if task.disabled:
                log.info("job %s is disabled after repeated failure; not re-registering", task.id)
                continue
            if task.recurring:
                self._register(task, self._first_run(task, now))
                continue
            try:
                run_at = datetime.fromisoformat(task.run_at)
            except ValueError:
                self.store.remove(task.id)
                continue
            if run_at <= now:
                decision = missed_decision(
                    kind=task.kind,
                    catch_up=task.catch_up,
                    last_run=task.run_at,
                    interval_seconds=0,
                    now=now,
                    grace_seconds=settings.cron_misfire_grace_seconds,
                )
                self.store.update(task.id, missed=task.missed + 1, last_outcome=decision)
                if decision == EXPIRE:
                    log.info("one-off job %s expired while down (%s)", task.id, task.run_at)
                    self.store.remove(task.id)
                    continue
            self._register(task, run_at)

    @staticmethod
    def _first_run(task: Task, now: datetime) -> datetime:
        """The next time a recurring job should fire, relative to `now`."""
        if task.kind == INTERVAL:
            return now + parse_every(task.every)
        hour, minute, _days = parse_calendar(task.at, task.weekdays)
        return datetime(now.year, now.month, now.day, hour, minute, tzinfo=now.tzinfo)

    def _register(self, task: Task, run_at: datetime) -> None:
        """Register one job, dispatching on its schedule kind.

        `coalesce=True` is what stops a recurring job that missed N windows from
        firing N times; combined with the explicit `missed_decision()`, the
        policy is one late run or one recorded skip — never a burst.
        """
        tz = run_at.tzinfo or ZoneInfo(settings.iris_timezone)
        if task.kind == INTERVAL:
            delta = parse_every(task.every)
            trigger: object = IntervalTrigger(seconds=int(delta.total_seconds()), timezone=tz)
        elif task.kind == CALENDAR:
            hour, minute, days = parse_calendar(task.at, task.weekdays)
            trigger = CronTrigger(hour=hour, minute=minute, day_of_week=days, timezone=tz)
        else:
            trigger = DateTrigger(run_date=run_at, timezone=tz)
        self.scheduler.add_job(
            self._run_task,
            trigger,
            args=[task],
            id=f"task-{task.id}",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=settings.cron_misfire_grace_seconds,
        )
        log.info("scheduled %s job %s (%s)", task.kind, task.id, task.describe())

    def schedule(self, when: str, instruction: str, session_id: str = "default") -> Task:
        """Parse, persist and register a new one-off task. Raises ValueError
        for unparseable/past times (surfaced as a tool error)."""
        run_at = parse_when(when)
        if run_at <= datetime.now(run_at.tzinfo):
            raise ValueError("that time has already passed")
        task = self.store.add(instruction=instruction, run_at=run_at, session_id=session_id)
        self._register(task, run_at)
        return task

    def schedule_interval(
        self, every: str, instruction: str, session_id: str = "default", *, catch_up: bool | None = None
    ) -> Task:
        """A job that repeats every N minutes/hours/days.

        Short intervals default to **not** catching up: a 15-minute job that was
        down for a day must not replay its backlog.
        """
        delta = parse_every(every)
        now = datetime.now(ZoneInfo(settings.iris_timezone))
        task = self.store.add(
            instruction=instruction,
            run_at=now + delta,
            session_id=session_id,
            kind=INTERVAL,
            every=every.strip(),
            catch_up=settings.cron_interval_catch_up if catch_up is None else catch_up,
        )
        self._register(task, now + delta)
        return task

    def schedule_calendar(
        self, at: str, instruction: str, session_id: str = "default", *, weekdays: str = ""
    ) -> Task:
        """A job at a time of day, optionally only on given weekdays."""
        hour, minute, _days = parse_calendar(at, weekdays)
        now = datetime.now(ZoneInfo(settings.iris_timezone))
        candidate = datetime(now.year, now.month, now.day, hour, minute, tzinfo=now.tzinfo)
        if candidate <= now:
            candidate += timedelta(days=1)
        task = self.store.add(
            instruction=instruction,
            run_at=candidate,
            session_id=session_id,
            kind=CALENDAR,
            at=at.strip(),
            weekdays=weekdays.strip(),
            catch_up=True,  # a daily anchor an hour late is still useful
        )
        self._register(task, candidate)
        return task

    def pending(self) -> list[Task]:
        return sorted(self.store.list(), key=lambda t: t.run_at)

    def cancel(self, task_id: str) -> bool:
        """Stop a job and forget it. Returns False if there was no such job."""
        job = self.scheduler.get_job(f"task-{task_id}")
        if job is not None:
            self.scheduler.remove_job(f"task-{task_id}")
        return self.store.remove(task_id)

    async def _run_task(self, task: Task) -> None:
        """Fire the instruction through the chat graph and deliver the reply.

        Never raises out of the job. A one-off is removed; a recurring job is
        kept and its bookkeeping updated. A recurring job that fails
        `cron_max_failures` times in a row is disabled rather than retried
        forever — the circuit-breaker principle applied to time.
        """
        ok = True
        try:
            reply = await self.graph.respond(task.instruction, session_id=task.session_id, origin="task")
        except Exception as exc:  # noqa: BLE001 - a fired task must never crash the scheduler
            ok = False
            log.warning("scheduled task %s failed: %s", task.id, exc)
            reply = f"I tried to handle a scheduled task but hit an error ({type(exc).__name__})."
        telegram = self.runtime.telegram
        if telegram is not None and telegram.connected and settings.owner_chat_id:
            try:
                await telegram.send_message(settings.owner_chat_id, f"⏰ {reply}")
            except Exception as exc:  # noqa: BLE001
                ok = False
                log.warning("delivering scheduled task %s failed: %s", task.id, exc)

        self._record_run(task, ok=ok)

    def _record_run(self, task: Task, *, ok: bool) -> None:
        """Persist the outcome of one run and decide whether to keep the job."""
        now = datetime.now(ZoneInfo(settings.iris_timezone)).isoformat(timespec="seconds")
        if not task.recurring:
            self.store.remove(task.id)
            return
        failures = 0 if ok else task.failures + 1
        disabled = failures >= settings.cron_max_failures
        self.store.update(
            task.id,
            runs=task.runs + 1,
            failures=failures,
            last_run=now,
            last_outcome="ok" if ok else "error",
            disabled=disabled,
        )
        if disabled:
            log.warning("disabling recurring job %s after %d consecutive failures", task.id, failures)
            job = self.scheduler.get_job(f"task-{task.id}")
            if job is not None:
                self.scheduler.remove_job(f"task-{task.id}")
