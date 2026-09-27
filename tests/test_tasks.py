"""Scheduled tasks: time parsing, JSON persistence, APScheduler registration."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from iris_ai.config import settings
from iris_ai.tasks import TaskScheduler, TaskStore, parse_when


def test_parse_when_iso():
    parsed = parse_when("2026-08-22T09:00")
    assert parsed.hour == 9
    assert parsed.tzinfo is not None


def test_parse_when_relative():
    tz = ZoneInfo(settings.iris_timezone)
    for text, unit in [
        ("in 3 days", "days"),
        ("in 90 minutes", "minutes"),
        ("in 2 hours", "hours"),
        ("in 1 week", "weeks"),
    ]:
        parsed = parse_when(text)
        now = datetime.now(tz)
        if unit == "days":
            assert (parsed - now) >= timedelta(days=2.5)
        elif unit == "minutes":
            assert (parsed - now) >= timedelta(minutes=89)
        elif unit == "hours":
            assert (parsed - now) >= timedelta(hours=1.5)
        else:
            assert (parsed - now) >= timedelta(days=6.5)


def test_parse_when_shorthand():
    tz = ZoneInfo(settings.iris_timezone)
    now = datetime.now(tz)
    tomorrow = parse_when("tomorrow 9:30")
    assert tomorrow.date() == now.date() + timedelta(days=1)
    assert tomorrow.hour == 9 and tomorrow.minute == 30

    today = parse_when("23:59")  # late today
    assert today.date() in (now.date(), now.date() + timedelta(days=1))
    assert today.hour == 23 and today.minute == 59


def test_parse_when_rejects_garbage():
    with pytest.raises(ValueError):
        parse_when("sometime soon")
    with pytest.raises(ValueError):
        parse_when("")


def test_task_store_roundtrip(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    run_at = datetime(2026, 9, 1, 9, 0, tzinfo=ZoneInfo("UTC"))
    task = store.add(instruction="remind me about the lease", run_at=run_at)
    store2 = TaskStore(tmp_path / "tasks.json")  # fresh instance reads disk
    tasks = store2.list()
    assert len(tasks) == 1
    assert tasks[0].id == task.id
    assert tasks[0].instruction == "remind me about the lease"
    assert store.remove(task.id) is True
    assert store.list() == []


def test_register_all_registers_future_and_drops_past(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    future = store.add(
        instruction="future task",
        run_at=datetime.now(ZoneInfo("UTC")) + timedelta(hours=3),
    )
    past = store.add(
        instruction="past task",
        run_at=datetime.now(ZoneInfo("UTC")) - timedelta(hours=1),
    )
    scheduler = AsyncIOScheduler(timezone=settings.iris_timezone)
    ts = TaskScheduler(store, runtime=None, graph=None, scheduler=scheduler)
    ts.register_all()
    assert scheduler.get_job(f"task-{future.id}") is not None
    assert scheduler.get_job(f"task-{past.id}") is None
    assert all(t.id != past.id for t in store.list()), "stale tasks must be dropped"


def test_schedule_rejects_past_time(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    scheduler = AsyncIOScheduler(timezone=settings.iris_timezone)
    ts = TaskScheduler(store, runtime=None, graph=None, scheduler=scheduler)
    with pytest.raises(ValueError, match="already passed"):
        ts.schedule("2020-01-01T00:00", "too late")


async def test_run_task_executes_and_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.add(
        instruction="send me the weekly summary",
        run_at=datetime.now(ZoneInfo("UTC")) + timedelta(hours=1),
    )
    scheduler = AsyncIOScheduler(timezone=settings.iris_timezone)

    class FakeGraph:
        async def respond(self, message, *, session_id, origin="owner"):
            self.got = (message, session_id, origin)
            return "here is the summary"

    class FakeTelegram:
        connected = True
        sent = []

        async def send_message(self, chat_id, text):
            self.sent.append(text)

    graph = FakeGraph()
    telegram = FakeTelegram()
    runtime = type("R", (), {"telegram": telegram})()
    monkeypatch.setattr(settings, "owner_chat_id", 424242)

    ts = TaskScheduler(store, runtime=runtime, graph=graph, scheduler=scheduler)
    await ts._run_task(task)

    assert graph.got == ("send me the weekly summary", "default", "task")
    assert telegram.sent and "here is the summary" in telegram.sent[0]
    assert store.list() == [], "fired task must be removed"
