"""Scheduler tests — digest formatting + job registration (no broker fired)."""

from __future__ import annotations

from pathlib import Path

from iris.agent.runtime import Runtime
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
from iris.sandbox import Sandbox
from iris.scheduler import build_scheduler, format_morning_brief


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
        skills=SkillLibrary(files),
        sandbox=Sandbox(tmp_path / "sandbox"),
    )
    scheduler = build_scheduler(runtime)
    jobs = {j.id for j in scheduler.get_jobs()}
    assert jobs == {"nightly-sleep", "morning-brief"}