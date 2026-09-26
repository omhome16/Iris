"""P6 — `iris cron list | add | rm`.

The CLI must be usable on a machine where the engine is **not running**: next-run
times come from the stored spec, never from a live scheduler. And it must not
grow a second grammar — `add` goes through the same parsers the agent's tool uses,
so a schedule the CLI accepts is one the agent could have created.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from typer.testing import CliRunner

from iris.cli.main import app
from iris.tasks import CALENDAR, INTERVAL, TaskStore, next_run_at

runner = CliRunner()


def _workspace(tmp_path: Path, monkeypatch) -> TaskStore:
    """Point the CLI at a throwaway workspace, like a fresh install."""
    from iris.config import settings

    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    return TaskStore(tmp_path / "config" / "tasks.json")


def test_cron_is_a_real_command():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "cron" in result.stdout


def test_an_empty_schedule_explains_the_built_ins(tmp_path: Path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "list"])
    assert result.exit_code == 0
    assert "no scheduled jobs" in result.stdout.lower()
    assert "nightly" in result.stdout.lower()  # says where the built-ins live


def test_list_shows_a_job_with_its_schedule_and_counters(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    store.add(instruction="water the plants", run_at=_now(), kind=INTERVAL, every="2 hours")
    store.add(instruction="weekly review", run_at=_now(), kind=CALENDAR, at="18:00", weekdays="sun")
    result = runner.invoke(app, ["cron", "list"])
    assert result.exit_code == 0
    assert "every 2 hours" in result.stdout
    assert "at 18:00 on sun" in result.stdout
    assert "water the plants" not in result.stdout  # the table shows metadata, not prose


def test_a_disabled_job_is_visible_as_disabled(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    task = store.add(instruction="dead", run_at=_now(), kind=INTERVAL, every="1 hour")
    store.update(task.id, disabled=True, failures=3)
    result = runner.invoke(app, ["cron", "list"])
    assert result.exit_code == 0
    assert "disabled" in result.stdout.lower()
    assert "3" in result.stdout  # the failure count is not hidden


def test_add_requires_exactly_one_schedule(tmp_path: Path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    none = runner.invoke(app, ["cron", "add", "-i", "do a thing"])
    assert none.exit_code == 2
    assert "pick exactly one schedule" in none.stdout

    both = runner.invoke(app, ["cron", "add", "--every", "1 hour", "--at", "09:00", "-i", "x"])
    assert both.exit_code == 2


def test_add_an_interval_job_persists_it(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "add", "--every", "15 minutes", "-i", "check the inbox"])
    assert result.exit_code == 0
    (task,) = store.list()
    assert task.kind == INTERVAL
    assert task.every == "15 minutes"
    assert task.instruction == "check the inbox"
    assert task.catch_up is False  # short intervals do not replay a backlog


def test_add_a_calendar_job_persists_it(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "add", "--at", "07:45", "-i", "morning review"])
    assert result.exit_code == 0
    (task,) = store.list()
    assert task.kind == CALENDAR
    assert task.at == "07:45"


def test_add_a_one_off_job_persists_it(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "add", "--once", "in 3 days", "-i", "renew the lease"])
    assert result.exit_code == 0
    (task,) = store.list()
    assert task.kind == "once"
    assert task.instruction == "renew the lease"


def test_add_rejects_a_past_time(tmp_path: Path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "add", "--once", "2020-01-01T09:00", "-i", "x"])
    assert result.exit_code == 1
    assert "passed" in result.stdout


def test_add_rejects_a_bad_interval_without_writing_anything(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "add", "--every", "every second", "-i", "x"])
    assert result.exit_code == 1
    assert store.list() == []


def test_the_cli_does_not_invent_a_second_grammar(tmp_path: Path, monkeypatch):
    """`add` must go through the same parsers the agent's tool uses."""
    store = _workspace(tmp_path, monkeypatch)
    runner.invoke(app, ["cron", "add", "--every", "2 hours", "-i", "x"])
    runner.invoke(app, ["cron", "add", "--at", "09:30", "-i", "y"])
    kinds = {t.kind for t in store.list()}
    assert kinds == {INTERVAL, CALENDAR}


def test_rm_deletes_the_job(tmp_path: Path, monkeypatch):
    store = _workspace(tmp_path, monkeypatch)
    task = store.add(instruction="x", run_at=_now(), kind=INTERVAL, every="1 hour")
    result = runner.invoke(app, ["cron", "rm", task.id])
    assert result.exit_code == 0
    assert store.list() == []


def test_rm_of_an_unknown_id_explains_the_built_ins(tmp_path: Path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "rm", "nope"])
    assert result.exit_code == 1
    assert "nightly" in result.stdout.lower()


def test_rm_without_an_id_is_a_usage_error(tmp_path: Path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    assert runner.invoke(app, ["cron", "rm"]).exit_code == 2


def test_an_unknown_action_is_a_usage_error(tmp_path: Path, monkeypatch):
    _workspace(tmp_path, monkeypatch)
    result = runner.invoke(app, ["cron", "summon"])
    assert result.exit_code == 2
    assert "list" in result.stdout


# ── next-run computation (pure, no scheduler) ────────────────────────────


def _now() -> datetime:
    return datetime(2026, 9, 24, 12, 0, tzinfo=ZoneInfo("UTC"))


def test_an_interval_jobs_next_run_is_relative_to_its_last_run(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.add(instruction="x", run_at=_now(), kind=INTERVAL, every="2 hours")
    store.update(task.id, last_run=(_now() - timedelta(minutes=30)).isoformat())
    assert next_run_at(store.get(task.id), now=_now()) == _now() + timedelta(minutes=90)


def test_a_job_that_missed_many_windows_has_one_next_run(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.add(instruction="x", run_at=_now() - timedelta(days=2), kind=INTERVAL, every="1 hour")
    nxt = next_run_at(store.get(task.id), now=_now())
    assert nxt is not None
    assert nxt > _now()  # in the future, not 48 runs behind


def test_a_calendar_job_rolls_to_tomorrow_when_today_has_passed(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.add(instruction="x", run_at=_now(), kind=CALENDAR, at="09:00")
    nxt = next_run_at(store.get(task.id), now=_now())
    assert nxt is not None and nxt.day == 25


def test_a_calendar_job_honours_weekdays(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    # 2026-09-24 is a Thursday; ask for Sunday (6) and expect the 27th.
    task = store.add(instruction="x", run_at=_now(), kind=CALENDAR, at="09:00", weekdays="sun")
    nxt = next_run_at(store.get(task.id), now=_now())
    assert nxt is not None
    assert nxt.weekday() == 6
    assert nxt.day == 27


def test_a_disabled_job_has_no_next_run(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.add(instruction="x", run_at=_now(), kind=INTERVAL, every="1 hour")
    store.update(task.id, disabled=True)
    assert next_run_at(store.get(task.id), now=_now()) is None


async def test_a_live_engine_can_be_told_to_reload(tmp_path: Path, monkeypatch):
    """`iris cron add|rm` edits the store from another process, so a running
    engine needs a way to pick the change up without a restart."""
    from types import SimpleNamespace

    from iris.api import app, cron_reload

    class Live:
        def __init__(self) -> None:
            self.reloaded = 0

        def register_all(self) -> None:
            self.reloaded += 1

        def pending(self):
            return []

    live = Live()
    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(tasks=live), raising=False)
    data = await cron_reload(_token=None)
    assert data == {"reloaded": True, "jobs": []}
    assert live.reloaded == 1


async def test_reload_says_so_when_there_is_no_scheduler(monkeypatch):
    from types import SimpleNamespace

    from iris.api import app, cron_reload

    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(tasks=None), raising=False)
    data = await cron_reload(_token=None)
    assert data["reloaded"] is False
    assert data["reason"]


def test_an_unreadable_row_returns_none_instead_of_raising(tmp_path: Path):
    store = TaskStore(tmp_path / "tasks.json")
    task = store.add(instruction="x", run_at=_now(), kind=CALENDAR, at="09:00")
    store.update(task.id, at="not a time")
    assert next_run_at(store.get(task.id), now=_now()) is None
