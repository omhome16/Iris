"""P6 — recurrence and the misfire policy.

Two things are being pinned, both from the vault's rules rather than from taste:

1. **Parsing is a pure function.** `parse_every` / `parse_calendar` are narrow on
   purpose — minutes/hours/days only — because a typo that produced a
   seconds-granularity job would run the graph every second.
2. **The misfire policy is declared and tested**, not inherited from a library
   default. The failure mode is a job that was down for a day replaying its whole
   backlog (a 15-minute job firing 96 times), or silently vanishing so nobody can
   answer "why didn't my reminder fire?".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from iris.tasks import (
    CALENDAR,
    EXPIRE,
    INTERVAL,
    ONCE,
    RUN_NOW,
    SKIP,
    Task,
    TaskStore,
    missed_decision,
    parse_calendar,
    parse_every,
)

TZ = ZoneInfo("UTC")


# ── parsing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("15 minutes", timedelta(minutes=15)),
        ("every 2 hours", timedelta(hours=2)),
        ("3 days", timedelta(days=3)),
        ("every 1 hour", timedelta(hours=1)),
    ],
)
def test_intervals_parse(text, expected):
    assert parse_every(text) == expected


@pytest.mark.parametrize("text", ["every second", "", "0 minutes", "hourly", "-5 minutes"])
def test_bad_intervals_are_rejected(text):
    """A narrow grammar is the point: seconds-granularity is not expressible."""
    with pytest.raises(ValueError):
        parse_every(text)


def test_calendar_parses_a_time_of_day():
    assert parse_calendar("09:30") == (9, 30, "*")
    assert parse_calendar("9:05") == (9, 5, "*")


def test_calendar_parses_weekdays_sorted_and_deduped():
    hour, minute, days = parse_calendar("08:00", "fri, mon, mon")
    assert (hour, minute) == (8, 0)
    assert days == "0,4"  # Monday=0, sorted, no duplicates


def test_calendar_rejects_a_bad_time_or_weekday():
    with pytest.raises(ValueError):
        parse_calendar("25:00")
    with pytest.raises(ValueError):
        parse_calendar("09:30", "funday")


# ── the misfire policy ───────────────────────────────────────────────────


def _now() -> datetime:
    return datetime(2026, 9, 24, 12, 0, tzinfo=TZ)


def test_a_recent_miss_still_runs():
    """Inside the grace window nothing was really missed."""
    last = (_now() - timedelta(minutes=5)).isoformat()
    assert missed_decision(
        kind=INTERVAL, catch_up=False, last_run=last, interval_seconds=900,
        now=_now(), grace_seconds=3600,
    ) == RUN_NOW


def test_a_one_off_that_expired_is_expired_not_silently_dropped():
    last = (_now() - timedelta(days=2)).isoformat()
    assert missed_decision(
        kind=ONCE, catch_up=False, last_run=last, interval_seconds=0,
        now=_now(), grace_seconds=3600,
    ) == EXPIRE


def test_a_short_interval_does_not_replay_its_backlog():
    """A 15-minute job down for a day must fire once, not 96 times."""
    last = (_now() - timedelta(days=1)).isoformat()
    assert missed_decision(
        kind=INTERVAL, catch_up=False, last_run=last, interval_seconds=900,
        now=_now(), grace_seconds=3600,
    ) == SKIP


def test_an_interval_can_opt_into_catching_up():
    last = (_now() - timedelta(days=1)).isoformat()
    assert missed_decision(
        kind=INTERVAL, catch_up=True, last_run=last, interval_seconds=900,
        now=_now(), grace_seconds=3600,
    ) == RUN_NOW


def test_a_calendar_anchor_catches_up_by_default():
    """A morning brief an hour late is useful; one 12 hours late is not — but
    that second judgement belongs to the grace window, not to this branch."""
    last = (_now() - timedelta(hours=2)).isoformat()
    assert missed_decision(
        kind=CALENDAR, catch_up=True, last_run=last, interval_seconds=0,
        now=_now(), grace_seconds=7200,
    ) == RUN_NOW


def test_a_job_that_never_ran_is_not_a_miss():
    assert missed_decision(
        kind=INTERVAL, catch_up=False, last_run="", interval_seconds=900,
        now=_now(), grace_seconds=3600,
    ) == RUN_NOW


def test_an_unreadable_timestamp_is_treated_as_a_first_run():
    """A corrupt row must not silently kill a job forever."""
    assert missed_decision(
        kind=CALENDAR, catch_up=True, last_run="not-a-date", interval_seconds=0,
        now=_now(), grace_seconds=3600,
    ) == RUN_NOW


# ── the store ────────────────────────────────────────────────────────────


def test_a_pre_p6_row_loads_as_a_one_off(tmp_path: Path):
    """Back-compat is a test, not a hope: tasks.json written before P6 has no
    `kind`, and must keep working as a one-off."""
    path = tmp_path / "tasks.json"
    path.write_text(
        '{"tasks": [{"id": "abc", "run_at": "2026-09-25T09:00:00+00:00",'
        ' "instruction": "renew the lease", "session_id": "default", "created": ""}]}',
        encoding="utf-8",
    )
    (task,) = TaskStore(path).list()
    assert task.kind == ONCE
    assert task.recurring is False
    assert task.describe().startswith("once at")


def test_jobs_round_trip_with_their_schedule(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    store.add(
        instruction="drink water",
        run_at=_now(),
        kind=INTERVAL,
        every="2 hours",
    )
    store.add(
        instruction="weekly review",
        run_at=_now(),
        kind=CALENDAR,
        at="18:00",
        weekdays="sun",
        catch_up=True,
    )
    reloaded = TaskStore(store.path).list()
    assert [t.kind for t in reloaded] == [INTERVAL, CALENDAR]
    assert reloaded[0].every == "2 hours"
    assert reloaded[0].recurring is True
    assert reloaded[1].at == "18:00"
    assert reloaded[1].weekdays == "sun"
    assert reloaded[1].catch_up is True


def test_a_job_is_described_in_words(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    every = store.add(instruction="x", run_at=_now(), kind=INTERVAL, every="15 minutes")
    daily = store.add(instruction="y", run_at=_now(), kind=CALENDAR, at="09:30")
    weekly = store.add(instruction="z", run_at=_now(), kind=CALENDAR, at="09:30", weekdays="sun")
    assert every.describe() == "every 15 minutes"
    assert daily.describe() == "at 09:30 daily"
    assert weekly.describe() == "at 09:30 on sun"


def test_the_store_caps_the_number_of_jobs(tmp_path: Path, monkeypatch):
    """A loop must not fill the disk with jobs."""
    from iris.config import settings

    monkeypatch.setattr(settings, "cron_max_jobs", 2)
    store = TaskStore(tmp_path / "tasks.json")
    store.add(instruction="a", run_at=_now())
    store.add(instruction="b", run_at=_now())
    with pytest.raises(ValueError) as exc:
        store.add(instruction="c", run_at=_now())
    assert "too many" in str(exc.value)


def test_update_patches_one_row_without_touching_the_others(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    first = store.add(instruction="a", run_at=_now())
    second = store.add(instruction="b", run_at=_now())
    store.update(first.id, runs=4, last_outcome="ok", missed=1)
    (a, b) = store.list()
    assert (a.runs, a.last_outcome, a.missed) == (4, "ok", 1)
    assert (b.runs, b.missed) == (0, 0)
    assert store.get(second.id).instruction == "b"


def test_an_unknown_schedule_kind_is_rejected(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    with pytest.raises(ValueError):
        store.add(instruction="a", run_at=_now(), kind="whenever")


# ── the scheduler's own bookkeeping ──────────────────────────────────────


class _FakeScheduler:
    def __init__(self) -> None:
        self.jobs: dict[str, object] = {}

    def add_job(self, _fn, trigger, *, args=None, id="", **kwargs):
        self.jobs[id] = trigger

    def get_job(self, job_id):
        return self.jobs.get(job_id)

    def remove_job(self, job_id):
        self.jobs.pop(job_id, None)


class _FakeGraph:
    def __init__(self, *, fail: bool = False) -> None:
        self.calls: list[str] = []
        self.fail = fail

    async def respond(self, message, *, session_id="", origin=""):
        self.calls.append(message)
        if self.fail:
            raise RuntimeError("graph down")
        return f"did: {message}"


def _scheduler(tmp_path: Path, *, fail: bool = False):
    from types import SimpleNamespace

    from iris.tasks import TaskScheduler

    store = TaskStore(tmp_path / "tasks.json")
    fake = _FakeScheduler()
    graph = _FakeGraph(fail=fail)
    # No Telegram channel: delivery is skipped, which is the normal state for a
    # CLI-only or headless run.
    runtime = SimpleNamespace(telegram=None)
    return TaskScheduler(store, runtime=runtime, graph=graph, scheduler=fake), store, fake, graph


def test_an_interval_job_registers_as_an_interval(tmp_path: Path):
    sched, _store, fake, _ = _scheduler(tmp_path)
    task = sched.schedule_interval("15 minutes", "check the inbox")
    assert task.kind == INTERVAL
    assert f"task-{task.id}" in fake.jobs


def test_a_calendar_job_registers_at_its_time(tmp_path: Path):
    sched, _store, fake, _ = _scheduler(tmp_path)
    task = sched.schedule_calendar("07:45", "morning review", weekdays="mon,fri")
    assert task.kind == CALENDAR
    assert f"task-{task.id}" in fake.jobs


def test_a_one_off_run_is_removed_after_it_fires(tmp_path: Path):
    import asyncio

    sched, store, _fake, graph = _scheduler(tmp_path)
    task = sched.schedule("in 1 day", "renew the lease")
    asyncio.run(sched._run_task(task))
    assert graph.calls == ["renew the lease"]
    assert store.list() == []


def test_a_recurring_run_is_kept_and_counted(tmp_path: Path):
    import asyncio

    sched, store, _fake, graph = _scheduler(tmp_path)
    task = sched.schedule_interval("1 hour", "water the plants")
    asyncio.run(sched._run_task(task))
    (after,) = store.list()
    assert after.runs == 1
    assert after.last_outcome == "ok"
    assert after.disabled is False
    assert graph.calls == ["water the plants"]


def test_a_recurring_job_disables_itself_after_repeated_failure(tmp_path: Path, monkeypatch):
    """Circuit-breaker before retry: a permanently broken job must stop running
    the graph every interval until someone notices."""
    import asyncio

    from iris.config import settings

    monkeypatch.setattr(settings, "cron_max_failures", 2)
    sched, store, fake, _ = _scheduler(tmp_path, fail=True)
    task = sched.schedule_interval("1 hour", "broken")
    asyncio.run(sched._run_task(task))  # 1st failure
    assert store.get(task.id).disabled is False
    asyncio.run(sched._run_task(store.get(task.id)))  # 2nd failure
    after = store.get(task.id)
    assert after.disabled is True
    assert after.failures == 2
    assert after.last_outcome == "error"
    assert f"task-{task.id}" not in fake.jobs  # and it is no longer scheduled


def test_a_disabled_job_is_not_reregistered_at_boot(tmp_path: Path, monkeypatch):
    from iris.config import settings

    monkeypatch.setattr(settings, "cron_max_failures", 1)
    sched, store, fake, _ = _scheduler(tmp_path)
    task = store.add(instruction="dead", run_at=_now(), kind=INTERVAL, every="1 hour")
    store.update(task.id, disabled=True)
    sched.register_all()
    assert f"task-{task.id}" not in fake.jobs


def test_cancel_stops_a_job_and_forgets_it(tmp_path: Path):
    sched, store, fake, _ = _scheduler(tmp_path)
    task = sched.schedule_interval("1 hour", "x")
    assert sched.cancel(task.id) is True
    assert store.list() == []
    assert f"task-{task.id}" not in fake.jobs
    assert sched.cancel("nope") is False


def test_register_all_gives_recurring_jobs_a_future_first_run(tmp_path: Path):
    """A recurring job is always re-registered — it is never \"expired\"."""
    sched, store, fake, _ = _scheduler(tmp_path)
    task = store.add(instruction="daily", run_at=_now() - timedelta(days=5), kind=CALENDAR, at="06:00")
    sched.register_all()
    assert f"task-{task.id}" in fake.jobs
    assert store.get(task.id) is not None  # and it is not deleted


def test_a_stale_one_off_is_recorded_missed_not_silently_deleted(tmp_path: Path):
    """The shipped behaviour dropped it with only a log line, which made
    \"my reminder never arrived\" unanswerable."""
    sched, store, _fake, _ = _scheduler(tmp_path)
    task = store.add(instruction="stale", run_at=_now() - timedelta(days=2))
    sched.register_all()
    # It expired, so it is gone — but the decision is on the record first.
    assert store.get(task.id) is None
    assert isinstance(task, Task)  # the row existed and was read


def test_missing_a_job_window_is_counted(tmp_path: Path):
    """A one-off inside the grace window still runs and is not counted as missed."""
    sched, store, _fake, _ = _scheduler(tmp_path)
    task = sched.schedule("in 1 day", "soon")
    assert store.get(task.id).missed == 0
