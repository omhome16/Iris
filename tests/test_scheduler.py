"""Scheduler tests — digest formatting + job registration (no broker fired)."""

from __future__ import annotations

from pathlib import Path

import pytest

from fakes import skill_registry
from iris.agent.runtime import Runtime
from iris.config import settings
from iris.memory.files import WorkspaceFiles
from iris.memory.forgetting import RotEntry
from iris.memory.llm import LLMClient
from iris.sandbox import Sandbox
from iris.scheduler import (
    _morning_brief,
    build_scheduler,
    format_morning_brief,
    owner_sleep_hour,
)


def test_morning_brief_empty_memory():
    brief = format_morning_brief({"chunks": []}, {"count": 0})
    assert "Memory is empty" in brief


def test_morning_brief_reports_decay_and_rot():
    retention = {
        "chunks": [
            {"content": "Went hiking", "retention": 0.9},
            {"content": "Old plan", "retention": 0.3},
        ]
    }
    rot = {"count": 1, "entries": [{"content": "Old plan", "retention": 0.3}]}
    brief = format_morning_brief(retention, rot, {"promoted": 2, "superseded": 1, "themes": 3})
    assert "2 promoted" in brief
    assert "below 50% retention" in brief
    assert "flagged as rot" in brief


def test_scheduler_registers_two_jobs(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    runtime = Runtime(
        files=files,
        llm=LLMClient(),  # type: ignore[arg-type] - never called
        index=None,  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(tmp_path / "sandbox"),
    )
    scheduler = build_scheduler(runtime)
    jobs = {j.id for j in scheduler.get_jobs()}
    assert jobs == {"nightly-sleep", "morning-brief"}


def test_build_scheduler_uses_onboarding_sleep_hour(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """The nightly sleep job must honor the owner's onboarding hour, not the
    env default."""
    monkeypatch.setattr(settings, "nightly_sleep_hour", 4)
    files = WorkspaceFiles(tmp_path)
    files.config_file().write_text(
        '{"onboarded": true, "sleep_pref": "2"}', encoding="utf-8"
    )
    runtime = Runtime(
        files=files,
        llm=LLMClient(),  # type: ignore[arg-type]
        index=None,  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(tmp_path / "sandbox"),
    )
    assert owner_sleep_hour(files.root) == 2
    scheduler = build_scheduler(runtime)
    job = scheduler.get_job("nightly-sleep")
    assert "hour='2'" in repr(job.trigger)


def test_owner_sleep_hour_falls_back_to_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "nightly_sleep_hour", 6)
    files = WorkspaceFiles(tmp_path)
    assert owner_sleep_hour(files.root) == 6
    files.config_file().write_text('{"sleep_pref": "oops"}', encoding="utf-8")
    assert owner_sleep_hour(files.root) == 6


async def test_morning_brief_handles_real_list_types(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression: _morning_brief must wrap the forgetting engine's raw list
    results into the dict shapes format_morning_brief expects."""

    class FakeForgetting:
        async def retention_report(self):
            return [{"content": "Old plan", "retention": 0.3}]

        async def rot_report(self):
            return [RotEntry("memory/2026-01-01.md", 0, "Old plan", 0.1, 200, "decayed")]

    class FakeTelegram:
        def __init__(self):
            self.sent = []

        @property
        def connected(self):
            return True

        async def send_message(self, chat_id: int, text: str) -> str:
            self.sent.append(text)
            return "ok"

    files = WorkspaceFiles(tmp_path)
    telegram = FakeTelegram()
    runtime = Runtime(
        files=files,
        llm=LLMClient(),  # type: ignore[arg-type] - never called
        index=None,  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=FakeForgetting(),  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(tmp_path / "sandbox"),
        telegram=telegram,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(settings, "owner_chat_id", 12345)
    await _morning_brief(runtime)
    assert len(telegram.sent) == 1
    assert "flagged as rot" in telegram.sent[0]
    assert "below 50% retention" in telegram.sent[0]
