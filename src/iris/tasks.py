"""Scheduled tasks — one-off future actions Iris commits to.

`schedule_task` (agent tool) parses a time and persists a task to
`workspace/config/tasks.json` (files are the source of truth). At boot the
TaskScheduler re-registers every pending future task as an APScheduler
one-off job (DateTrigger). When a job fires, the instruction runs through the
chat graph exactly as if the owner had sent it, the reply is delivered over
Telegram when the channel and owner chat are known, and the task is removed.

Accepted `when` formats (timezone = settings.iris_timezone):
  - ISO 8601:           2026-08-22T09:00  (with or without offset)
  - relative:           in 3 days · in 90 minutes · in 2 weeks
  - shorthand:          tomorrow · tomorrow 9:30 · today 21:30 · 9:30
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
from apscheduler.triggers.date import DateTrigger

from iris.config import settings

log = logging.getLogger("iris.tasks")

_RELATIVE_RE = re.compile(
    r"^in (\d+) (minute|minutes|hour|hours|day|days|week|weeks)$", re.IGNORECASE
)
_TIME_RE = re.compile(r"^(today|tomorrow)?\s*(\d{1,2}):(\d{2})$", re.IGNORECASE)
_DAY_ONLY_RE = re.compile(r"^(today|tomorrow)$", re.IGNORECASE)

DEFAULT_REMINDER_HOUR = 9  # bare "tomorrow" / "today" default time


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


@dataclass(slots=True)
class Task:
    id: str
    run_at: str  # ISO 8601, aware
    instruction: str
    session_id: str = "default"
    created: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


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

    def list(self) -> list[Task]:
        return [
            Task(id=str(t.get("id")), run_at=str(t.get("run_at")),
                 instruction=str(t.get("instruction")),
                 session_id=str(t.get("session_id", "default")),
                 created=str(t.get("created", "")))
            for t in self._load()
        ]

    def add(self, *, instruction: str, run_at: datetime, session_id: str = "default") -> Task:
        now = datetime.now(ZoneInfo(settings.iris_timezone))
        task = Task(
            id=uuid.uuid4().hex[:12],
            run_at=run_at.isoformat(),
            instruction=instruction,
            session_id=session_id,
            created=now.isoformat(timespec="seconds"),
        )
        tasks = self._load()
        tasks.append(task.to_dict())
        self._save(tasks)
        return task

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
        """Re-register every pending future task; drop stale ones."""
        tz = ZoneInfo(settings.iris_timezone)
        now = datetime.now(tz)
        for task in self.store.list():
            try:
                run_at = datetime.fromisoformat(task.run_at)
            except ValueError:
                self.store.remove(task.id)
                continue
            if run_at <= now:
                log.info("dropping past task %s (%s)", task.id, task.run_at)
                self.store.remove(task.id)
                continue
            self._register(task, run_at)

    def _register(self, task: Task, run_at: datetime) -> None:
        self.scheduler.add_job(
            self._run_task,
            DateTrigger(run_date=run_at, timezone=run_at.tzinfo or ZoneInfo(settings.iris_timezone)),
            args=[task],
            id=f"task-{task.id}",
            replace_existing=True,
            misfire_grace_time=3600,
        )
        log.info("scheduled task %s at %s", task.id, task.run_at)

    def schedule(self, when: str, instruction: str, session_id: str = "default") -> Task:
        """Parse, persist and register a new one-off task. Raises ValueError
        for unparseable/past times (surfaced as a tool error)."""
        run_at = parse_when(when)
        if run_at <= datetime.now(run_at.tzinfo):
            raise ValueError("that time has already passed")
        task = self.store.add(instruction=instruction, run_at=run_at, session_id=session_id)
        self._register(task, run_at)
        return task

    def pending(self) -> list[Task]:
        return sorted(self.store.list(), key=lambda t: t.run_at)

    async def _run_task(self, task: Task) -> None:
        """Fire the instruction through the chat graph, deliver the reply,
        then forget the task. Never raises out of the job."""
        try:
            reply = await self.graph.respond(task.instruction, session_id=task.session_id, origin="task")
        except Exception as exc:  # noqa: BLE001 - a fired task must never crash the scheduler
            log.warning("scheduled task %s failed: %s", task.id, exc)
            reply = f"I tried to handle a scheduled task but hit an error ({type(exc).__name__})."
        telegram = self.runtime.telegram
        if telegram is not None and telegram.connected and settings.owner_chat_id:
            try:
                await telegram.send_message(settings.owner_chat_id, f"⏰ {reply}")
            except Exception as exc:  # noqa: BLE001
                log.warning("delivering scheduled task %s failed: %s", task.id, exc)
        self.store.remove(task.id)
