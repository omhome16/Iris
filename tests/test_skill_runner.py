"""The skill script boundary — the only way a skill's code runs.

Everything here is about what *cannot* happen: a script outside its own
directory, a script the judgment gate refuses, a run without the owner's
approval, an inherited API key, an unbounded wait. One test does run a real
subprocess (stdlib only, no network) because the point of the boundary is that
it actually executes something.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from fakes import FakeJev
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.skills.registry import SkillRegistry
from iris_ai.skills.runner import (
    ScriptError,
    pre_screen,
    resolve_script,
    run_script,
)

SKILL_MD = """\
---
name: pdf-notes
description: Extract notes from a document. Use when handling documents.
allowed-tools: Read skill_run
metadata:
  iris-triggers: "extract notes"
---

Run scripts/extract.py on a file in the sandbox.
"""


def _install(tmp_path: Path, script_name: str = "extract.py", body: str = "print('ok')\n"):
    """A workspace with one standard-format skill that has a script."""
    files = WorkspaceFiles(tmp_path)
    directory = files.skills_dir() / "pdf-notes"
    (directory / "scripts").mkdir(parents=True)
    (directory / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (directory / "scripts" / script_name).write_text(body, encoding="utf-8")
    registry = SkillRegistry(files, builtin_dir=None, entry_points=lambda: [])
    return files, registry, registry.get("pdf-notes")


# ── resolution ──────────────────────────────────────────────────────────────

def test_resolve_accepts_a_script_inside_the_skill(tmp_path):
    _files, _registry, skill = _install(tmp_path)
    path = resolve_script(skill, "scripts/extract.py")
    assert path.is_file()
    assert path.parent.name == "scripts"


@pytest.mark.parametrize(
    "script",
    [
        "../outside.py",
        "scripts/../../outside.py",
        "C:\\Windows\\system32\\cmd.exe",
        "/etc/passwd",
        "scripts\\\\extract.py",
        "",
    ],
)
def test_resolve_rejects_anything_outside_the_skill(tmp_path, script):
    _files, _registry, skill = _install(tmp_path)
    with pytest.raises(ScriptError):
        resolve_script(skill, script)


def test_resolve_rejects_a_file_outside_scripts(tmp_path):
    """`SKILL.md` itself is not executable, and neither is anything else at the
    skill root: the boundary is the `scripts/` directory."""
    _files, _registry, skill = _install(tmp_path)
    (Path(skill.root) / "helper.py").write_text("print('hi')\n", encoding="utf-8")
    with pytest.raises(ScriptError):
        resolve_script(skill, "helper.py")


def test_resolve_refuses_a_learned_flat_skill(tmp_path):
    """A learned procedure has no root and no scripts: nothing to run."""
    files = WorkspaceFiles(tmp_path)
    (files.skills_dir() / "learned.json").write_text(
        '{"name": "learned", "description": "d", "procedure": "p"}', encoding="utf-8"
    )
    registry = SkillRegistry(files, builtin_dir=None, entry_points=lambda: [])
    skill = registry.get("learned")
    with pytest.raises(ScriptError, match="no script directory"):
        resolve_script(skill, "scripts/anything.py")


# ── the deterministic pre-screen ────────────────────────────────────────────

@pytest.mark.parametrize(
    "body, needle",
    [
        ("import os\nprint(os.environ['IRIS_API_TOKEN'])\n", "credentials"),
        ("import socket\nsocket.create_connection(('example.com', 80))\n", "network"),
        ("import subprocess\nsubprocess.run(['ls'])\n", "process"),
        ("eval('1+1')\n", "dynamic execution"),
        ("import shutil\nshutil.rmtree('/tmp/x')\n", "filesystem"),
    ],
)
def test_pre_screen_flags_what_the_owner_should_be_told(body, needle):
    findings = pre_screen(body)
    assert any(needle in f for f in findings), findings


def test_pre_screen_is_quiet_for_a_harmless_script():
    assert pre_screen("from pathlib import Path\nprint(Path('notes.txt').read_text())\n") == []


def test_pre_screen_scans_code_not_prose():
    """Regression, caught by a live judgment call: a docstring saying "offline by
    design, no network access" produced a 'uses the network' finding — which then
    travelled into the JEV state and dragged a safe script below the gate. Only
    executable code is evidence."""
    prose = '''
"""Offline by design: no network access, no os.environ reads, never calls eval()."""

# This script never uses subprocess or socket either.
LABEL = "http://example.com is a URL string, not a request"
print(LABEL)
'''
    assert pre_screen(prose) == []


def test_pre_screen_flags_an_escaped_open():
    assert "touches the filesystem" in pre_screen("open('/etc/passwd').read()\n")
    assert "touches the filesystem" in pre_screen("open('../../secrets.txt').read()\n")
    assert pre_screen("open('notes.txt').read()\n") == []


# ── the judgment gate ───────────────────────────────────────────────────────

async def test_guard_blocks_a_script_it_judges_unsafe(tmp_path):
    from iris_ai.skills import guard

    _files, _registry, skill = _install(
        tmp_path, body="import os\nprint(os.environ['IRIS_API_TOKEN'])\n"
    )
    verdict = await guard.screen_script(
        FakeJev(nouls={"safe": 0.05}), skill=skill, script="scripts/extract.py"
    )
    assert verdict.screened is True
    assert verdict.allowed is False
    assert "0.05" in verdict.reason


async def test_guard_allows_a_script_it_judges_safe(tmp_path):
    from iris_ai.skills import guard

    _files, _registry, skill = _install(tmp_path)
    verdict = await guard.screen_script(
        FakeJev(nouls={"safe": 0.97}), skill=skill, script="scripts/extract.py"
    )
    assert verdict.screened is True
    assert verdict.allowed is True


async def test_guard_reports_that_it_did_not_run_without_a_judgment_layer(tmp_path):
    from iris_ai.skills import guard

    _files, _registry, skill = _install(tmp_path)
    verdict = await guard.screen_script(None, skill=skill, script="scripts/extract.py")
    assert verdict.screened is False
    assert verdict.allowed is True  # approval still stands in front of it
    assert "unavailable" in verdict.reason


async def test_guard_treats_a_failed_request_as_unscreened(tmp_path):
    from iris_ai.skills import guard

    _files, _registry, skill = _install(tmp_path)
    verdict = await guard.screen_script(
        FakeJev(fail=True), skill=skill, script="scripts/extract.py"
    )
    assert verdict.screened is False
    assert verdict.allowed is True


# ── execution ───────────────────────────────────────────────────────────────

async def test_run_executes_a_real_script(tmp_path):
    _files, _registry, skill = _install(tmp_path, body="print('hello from a skill')\n")
    result = await run_script(skill, "scripts/extract.py")
    assert result.ok is True
    assert result.exit_code == 0
    assert "hello from a skill" in result.stdout


async def test_run_passes_arguments(tmp_path):
    _files, _registry, skill = _install(tmp_path, body="import sys\nprint(' '.join(sys.argv[1:]))\n")
    result = await run_script(skill, "scripts/extract.py", args=["a", "b"])
    assert result.stdout.strip() == "a b"


async def test_run_does_not_inherit_the_environment(tmp_path, monkeypatch):
    """The whole reason scripts are runnable at all: a script cannot read the
    owner's keys out of the environment, because there are none to read."""
    body = (
        "import os\n"
        "print('TOKEN=' + os.environ.get('IRIS_API_TOKEN', 'absent'))\n"
        "print('KEY=' + os.environ.get('TYPESAFE_API_KEY', 'absent'))\n"
        "print('CWD=' + os.getcwd())\n"
    )
    monkeypatch.setenv("IRIS_API_TOKEN", "super-secret")
    monkeypatch.setenv("TYPESAFE_API_KEY", "super-secret")
    _files, _registry, skill = _install(tmp_path, body=body)
    result = await run_script(skill, "scripts/extract.py")
    assert "TOKEN=absent" in result.stdout
    assert "KEY=absent" in result.stdout
    assert "super-secret" not in result.stdout
    # …and it runs *inside* its own skill directory, not the repo.
    assert str(Path(skill.root).resolve()) in result.stdout.replace("/", "\\").replace("\\\\", "\\")


async def test_run_keeps_the_interpreter_on_the_path(tmp_path):
    """Stripping the environment must not strip the ability to be a process."""
    _files, _registry, skill = _install(tmp_path, body="import sys\nprint(sys.version_info.major)\n")
    result = await run_script(skill, "scripts/extract.py")
    assert result.ok is True
    assert result.stdout.strip().startswith("3")


async def test_run_kills_a_script_that_overstays_its_timeout(tmp_path):
    body = "import time\ntime.sleep(30)\nprint('never')\n"
    _files, _registry, skill = _install(tmp_path, body=body)
    result = await run_script(skill, "scripts/extract.py", timeout=1.0)
    assert result.ok is False
    assert result.timed_out is True
    assert "never" not in result.stdout


async def test_run_caps_the_output(tmp_path):
    _files, _registry, skill = _install(tmp_path, body="print('x' * 5000)\n")
    result = await run_script(skill, "scripts/extract.py", max_output=500)
    assert len(result.stdout) < 800
    assert "truncated" in result.stdout


async def test_run_reports_a_failing_script_as_data(tmp_path):
    body = "import sys\nprint('boom', file=sys.stderr)\nsys.exit(2)\n"
    _files, _registry, skill = _install(tmp_path, body=body)
    result = await run_script(skill, "scripts/extract.py")
    assert result.ok is False
    assert result.exit_code == 2
    assert "boom" in result.stderr


async def test_run_honours_the_manifest_timeout_when_it_is_smaller(tmp_path):
    from iris_ai.memory.files import WorkspaceFiles  # noqa: F401 - clarity for readers

    body = "import time\ntime.sleep(5)\n"
    _files, registry, _skill = _install(tmp_path, body=body)
    skill = registry.get("pdf-notes")
    skill.timeout_seconds = 1.0
    result = await run_script(skill, "scripts/extract.py")  # no explicit timeout
    assert result.timed_out is True


def test_the_interpreter_is_the_one_running_iris():
    from iris_ai.skills.runner import _python
    assert _python() == sys.executable


def test_run_works_on_the_selector_loop_the_api_selects(tmp_path):
    """Regression, found by the full suite rather than by this file: `iris_ai.api`
    sets the Windows Selector event loop at import (psycopg needs it), and
    `asyncio` subprocess support is unimplemented on it. A skill script must run
    in the API process too, so execution goes through a worker thread."""
    if sys.platform != "win32":
        pytest.skip("the policy is Windows-specific")

    import asyncio

    async def scenario() -> None:
        _files, _registry, skill = _install(tmp_path, body="print('selector loop ok')\n")
        result = await run_script(skill, "scripts/extract.py")
        assert result.ok is True, result.error
        assert "selector loop ok" in result.stdout

    previous = asyncio.get_event_loop_policy()
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(scenario())
    finally:
        asyncio.set_event_loop_policy(previous)
