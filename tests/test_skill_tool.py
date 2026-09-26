"""The `skill_run` tool: four gates, and evidence for every outcome.

These tests call the tool's handler through `dispatch` (the real path a model's
tool call takes), with the graph's interrupt replaced by a stub, so what is
under test is the gate order rather than the LLM plumbing.
"""

from __future__ import annotations

from pathlib import Path

from fakes import FakeJev, skill_registry
from iris.agent import tools as tools_module
from iris.agent.runtime import Runtime
from iris.agent.tools import dispatch, get_tools
from iris.memory.files import WorkspaceFiles
from iris.sandbox import Sandbox

SKILL_MD = """\
---
name: notes
description: Turn a local document into notes.
allowed-tools: skill_run
metadata:
  iris-triggers: "take notes"
---

Run scripts/extract.py.
"""


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


class StubLLM:
    async def complete(self, *args, **kwargs):
        return ""

    async def embed(self, texts):
        return [[0.1]] * len(texts)

    async def embed_one(self, text):
        return [0.1]


def _runtime(tmp_path: Path, *, body: str = "print('notes done')\n", jev=None) -> Runtime:
    files = WorkspaceFiles(tmp_path)
    directory = files.skills_dir() / "notes"
    (directory / "scripts").mkdir(parents=True)
    (directory / "SKILL.md").write_text(SKILL_MD, encoding="utf-8")
    (directory / "scripts" / "extract.py").write_text(body, encoding="utf-8")
    return Runtime(
        files=files,
        llm=StubLLM(),  # type: ignore[arg-type]
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(files.root / "sandbox"),
        jev=jev,
    )


def test_skill_run_is_registered():
    assert "skill_run" in tools_module.TOOL_NAMES


async def test_unknown_skill_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    runtime = _runtime(tmp_path)
    out = await dispatch(runtime, "skill_run", {"name": "nope", "script": "scripts/extract.py"})
    assert '"ok": false' in out and "no skill named" in out


async def test_a_script_outside_the_skill_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    runtime = _runtime(tmp_path)
    out = await dispatch(runtime, "skill_run", {"name": "notes", "script": "../escape.py"})
    assert '"ok": false' in out
    assert "not inside" in out or "unsafe" in out


async def test_the_guard_refusal_blocks_before_any_approval(tmp_path, monkeypatch):
    """A judgment refusal is not overridable: the owner is never even asked, and
    nothing is executed."""
    asked: list[dict] = []
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: asked.append(payload) or "approved")
    marker = tmp_path / "ran.txt"
    runtime = _runtime(
        tmp_path,
        body=f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
        jev=FakeJev(nouls={"safe": 0.02}),
    )
    out = await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    assert '"ok": false' in out
    assert "safety judgment" in out
    assert asked == [], "the owner must not be asked to approve something already refused"
    assert not marker.exists(), "a refused script must not have run"


async def test_approval_is_required_and_a_cancel_runs_nothing(tmp_path, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: seen.append(payload) or "cancelled")
    marker = tmp_path / "ran.txt"
    runtime = _runtime(
        tmp_path,
        body=f"from pathlib import Path\nPath({str(marker)!r}).write_text('ran')\n",
    )
    out = await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    assert '"ok": false' in out and "cancelled" in out
    assert not marker.exists()
    # the prompt carries what the owner needs to decide
    assert seen and seen[0]["action"] == "skill_run"
    assert seen[0]["skill"] == "notes"
    assert "findings" in seen[0] and "guard" in seen[0]


async def test_the_approval_prompt_carries_the_pre_screen_findings(tmp_path, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: seen.append(payload) or "cancelled")
    runtime = _runtime(tmp_path, body="import os\nprint(os.environ.get('HOME'))\n")
    await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    assert any("credentials" in f for f in seen[0]["findings"])


async def test_the_approval_prompt_shows_the_arguments(tmp_path, monkeypatch):
    """The runner cannot know what a path means, so the owner has to see it."""
    seen: list[dict] = []
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: seen.append(payload) or "cancelled")
    runtime = _runtime(tmp_path)
    await dispatch(
        runtime,
        "skill_run",
        {"name": "notes", "script": "scripts/extract.py", "args": ["pages/a.html", "out.md"]},
    )
    assert seen[0]["args"] == ["pages/a.html", "out.md"]


async def test_an_approved_run_executes_and_returns_stdout(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    runtime = _runtime(tmp_path, body="print('notes done')\n")
    out = await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    assert '"ok": true' in out
    assert "notes done" in out


async def test_a_failing_script_comes_back_as_data(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    runtime = _runtime(tmp_path, body="import sys\nprint('nope', file=sys.stderr)\nsys.exit(3)\n")
    out = await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    assert '"ok": false' in out
    assert "code 3" in out


async def test_every_outcome_is_recorded_in_the_turn_trace(tmp_path, monkeypatch):
    from iris import turnlog

    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    runtime = _runtime(tmp_path, body="print('done')\n")
    with turnlog.collect() as log:
        await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    events = [e for e in log.judgments if e["kind"] == "skill_run"]
    assert events and events[0]["event"] == "ran"
    assert events[0]["skill"] == "notes"


async def test_the_guard_receives_the_state_it_needs(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    jev = FakeJev(nouls={"safe": 0.99})
    runtime = _runtime(tmp_path, jev=jev)
    await dispatch(runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"})
    state = jev.calls[0]["state"]
    assert state["skill"]["name"] == "notes"
    assert "scripts/extract.py" in state["script"]["path"]
    assert "source" in state["script"]


async def test_a_skill_can_be_forbidden_from_running_scripts_by_its_own_policy(tmp_path, monkeypatch):
    """`allowed-tools` narrows the turn: a skill that does not list `skill_run`
    cannot run one, even if it ships the file."""
    monkeypatch.setattr(tools_module, "interrupt", lambda payload: "approved")
    runtime = _runtime(tmp_path)
    out = await dispatch(
        runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"}, active_skills=["notes"]
    )
    # `notes` allows skill_run, so this one passes…
    assert '"ok": true' in out

    # …until the manifest stops listing it. Edited on disk, not in memory: the
    # registry re-reads its sources, so the manifest is the only truth.
    manifest = Path(runtime.skills.get("notes").root) / "SKILL.md"
    manifest.write_text(manifest.read_text(encoding="utf-8").replace("allowed-tools: skill_run", "allowed-tools: Read"), encoding="utf-8")
    out = await dispatch(
        runtime, "skill_run", {"name": "notes", "script": "scripts/extract.py"}, active_skills=["notes"]
    )
    assert '"ok": false' in out and "restricts this turn" in out


def test_the_tool_is_offered_to_the_model_in_owner_sessions(tmp_path):
    runtime = _runtime(tmp_path)
    names = {t.name for t in get_tools(runtime)}
    assert "skill_run" in names
