"""`iris skills` — the read-only CLI over the registry.

`validate` is the machine interface: its exit code is what a CI job or an owner
gates on, so a malformed manifest has to fail the command rather than print a
warning nobody notices.

Commands are driven through typer's runner (fast, in-process) with one
subprocess case at the end, which is what proves the installed command works and
that an error exit code really reaches the shell.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iris.cli.main import app
from iris.config import settings

SKILL_MD = """\
---
name: pdf-notes
description: Extract notes from a document. Use when handling documents.
allowed-tools: memory_search skill_run
metadata:
  iris-triggers: "extract notes"
  version: "1.2"
---

# PDF notes

Step 1: open the document.
"""


@pytest.fixture()
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "workspace"
    skills = root / "skills"
    (skills / "pdf-notes" / "scripts").mkdir(parents=True)
    (skills / "pdf-notes" / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (skills / "pdf-notes" / "scripts" / "extract.py").write_text("print('ok')\n", encoding="utf-8")
    (skills / "learned.json").write_text(
        '{"name": "learned", "description": "a learned procedure", "procedure": "p", "triggers": ["t"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(settings, "workspace_dir", str(root))
    monkeypatch.setattr(settings, "skills_builtin_dir", "")  # repo builtins out of the picture
    return root


def _run(*args: str):
    return CliRunner().invoke(app, ["skills", *args])


def test_list_shows_every_source(workspace: Path):
    result = _run("list")
    assert result.exit_code == 0, result.output
    assert "pdf-notes" in result.output and "learned" in result.output
    assert "workspace" in result.output


def test_list_marks_disabled_and_scored_skills(workspace: Path):
    result = _run("list")
    assert result.exit_code == 0
    # the learned skill's success score is part of the table
    assert "0.5" in result.output


def test_show_prints_the_manifest_and_the_procedure(workspace: Path):
    result = _run("show", "pdf-notes")
    assert result.exit_code == 0, result.output
    assert "Extract notes" in result.output
    assert "Step 1: open the document." in result.output
    assert "memory_search" in result.output
    assert "1.2" in result.output


def test_show_lists_the_scripts_a_skill_ships(workspace: Path):
    result = _run("show", "pdf-notes")
    assert "scripts/extract.py" in result.output


def test_show_for_an_unknown_skill_exits_nonzero(workspace: Path):
    result = _run("show", "no-such-skill")
    assert result.exit_code != 0
    assert "no-such-skill" in result.output


def test_validate_is_clean_for_a_clean_workspace(workspace: Path):
    result = _run("validate")
    assert result.exit_code == 0, result.output


def test_validate_fails_on_a_malformed_skill(workspace: Path):
    broken = workspace / "skills" / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: Broken_Name\ndescription: d\n---\nb\n", encoding="utf-8")
    result = _run("validate")
    assert result.exit_code == 1
    assert "Broken_Name" in result.output


def test_validate_fails_on_an_unknown_tool_name(workspace: Path):
    (workspace / "skills" / "pdf-notes" / "SKILL.md").write_text(
        SKILL_MD.replace("allowed-tools: memory_search skill_run", "allowed-tools: Teleport"),
        encoding="utf-8",
    )
    result = _run("validate")
    assert result.exit_code == 1
    assert "Teleport" in result.output


def test_validate_reports_a_name_conflict_as_a_warning_not_a_failure(workspace: Path, tmp_path: Path):
    """Two skills with one name is worth showing, but it is resolved — not an error."""
    builtin = tmp_path / "builtin" / "pdf-notes"
    builtin.mkdir(parents=True)
    (builtin / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    settings.skills_builtin_dir = str(tmp_path / "builtin")
    result = _run("validate")
    assert result.exit_code == 0, result.output
    assert "shadowed" in result.output


def test_the_command_registry_grew_by_exactly_one():
    """Each phase added exactly one command: P4 `skills`, P5 `agents`, P6 `cron`,
    P7 `tools`, P8 `guards`. (`tests/test_cli.py` owns the canonical exact-set
    assertion — this one exists to make the *growth* visible in a diff.)"""
    commands = {c.name or c.callback.__name__ for c in app.registered_commands}
    assert commands == {
        "agents",
        "chat",
        "cron",
        "doctor",
        "guards",
        "skills",
        "tools",
        "version",
    }


def test_the_installed_command_reaches_a_shell_exit_code(workspace: Path):
    """The real process, not the runner: `iris skills validate` must be usable
    as a gate (`if iris skills validate; then …`)."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "WORKSPACE_DIR": str(workspace),
        "SKILLS_BUILTIN_DIR": "",
        "PYTHONIOENCODING": "utf-8",
    }
    ok = subprocess.run(
        [sys.executable, "-m", "iris.cli.main", "skills", "validate"],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert ok.returncode == 0, ok.stdout + ok.stderr

    (workspace / "skills" / "broken").mkdir()
    (workspace / "skills" / "broken" / "SKILL.md").write_text(
        "---\nname: nope\n---\nbody\n", encoding="utf-8"
    )
    bad = subprocess.run(
        [sys.executable, "-m", "iris.cli.main", "skills", "validate"],
        capture_output=True,
        text=True,
        timeout=180,
        env=env,
    )
    assert bad.returncode == 1
