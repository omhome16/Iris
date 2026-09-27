"""The `computer` tool and the session that fences it."""

from __future__ import annotations

import json

from fakes import skill_registry
from iris_ai.agent.runtime import Runtime
from iris_ai.agent.tools import get_tools
from iris_ai.computer import Action, ActionKind, Observation
from iris_ai.computer.audit import ActionLog
from iris_ai.computer.permissions import PermissionModel
from iris_ai.computer.session import Computer
from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.llm import LLMClient
from iris_ai.sandbox import Sandbox


class RecordingProvider:
    name = "recording"

    def __init__(self, *, ok: bool = True, reason: str = "") -> None:
        self.actions: list[Action] = []
        self._ok = ok
        self._reason = reason

    def availability(self):
        return self._ok, self._reason

    async def perform(self, action):
        self.actions.append(action)
        return Observation(kind=action.kind, ok=True, detail=f"did {action.kind.value}")


class NoopLLM(LLMClient):
    async def complete(self, messages, **kwargs):
        return "ok"

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        return "ok", [], ""


class StubIndex:
    async def search(self, *args, **kwargs):
        return []

    async def escalate(self, *args, **kwargs):
        return []

    async def stats(self):
        return {"total_chunks": 0, "by_origin": {}}


def _machine(tmp_path, provider, permissions) -> Computer:
    return Computer(
        provider=provider,
        permissions=permissions,
        log=ActionLog(tmp_path / "config" / "actions.jsonl"),
    )


def _runtime(files: WorkspaceFiles, machine: Computer) -> Runtime:
    runtime = Runtime(
        files=files,
        llm=NoopLLM(),
        index=StubIndex(),  # type: ignore[arg-type]
        reindexer=None,  # type: ignore[arg-type]
        dreams=None,  # type: ignore[arg-type]
        forgetting=None,  # type: ignore[arg-type]
        skills=skill_registry(files),
        sandbox=Sandbox(files.root / "sandbox"),
    )
    runtime.computer = machine
    return runtime


def _tool(runtime, name):
    return next(t for t in get_tools(runtime) if t.name == name)


# ── registration is the first boundary ──────────────────────────────────────


def test_computer_is_absent_unless_the_owner_enabled_it(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "computer_enabled", False)
    files = WorkspaceFiles(tmp_path)
    runtime = _runtime(files, _machine(tmp_path, RecordingProvider(), PermissionModel()))
    assert "computer" not in {t.name for t in get_tools(runtime)}


def test_computer_appears_once_enabled(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "computer_enabled", True)
    files = WorkspaceFiles(tmp_path)
    runtime = _runtime(files, _machine(tmp_path, RecordingProvider(), PermissionModel()))
    assert "computer" in {t.name for t in get_tools(runtime)}


# ── refusals that never reach an approval prompt ────────────────────────────


async def test_no_provider_is_a_safe_refusal_not_an_interrupt(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "computer_enabled", True)
    files = WorkspaceFiles(tmp_path)
    provider = RecordingProvider(ok=False, reason="no driver configured")
    runtime = _runtime(files, _machine(tmp_path, provider, PermissionModel()))
    result = json.loads(await _tool(runtime, "computer").handler(action="screenshot"))
    assert result["ok"] is False
    assert "no driver" in result["error"]
    assert provider.actions == []


async def test_a_disallowed_navigate_is_refused_before_any_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "computer_enabled", True)
    files = WorkspaceFiles(tmp_path)
    provider = RecordingProvider()
    runtime = _runtime(files, _machine(tmp_path, provider, PermissionModel()))
    result = json.loads(
        await _tool(runtime, "computer").handler(action="navigate", target="https://example.com")
    )
    assert result["ok"] is False
    assert "no navigation targets" in result["error"]
    assert provider.actions == []


async def test_an_unknown_action_is_refused_with_the_vocabulary(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "computer_enabled", True)
    files = WorkspaceFiles(tmp_path)
    runtime = _runtime(files, _machine(tmp_path, RecordingProvider(), PermissionModel()))
    result = json.loads(await _tool(runtime, "computer").handler(action="sudo"))
    assert result["ok"] is False
    assert "screenshot" in result["error"]


# ── the session that fences the action ──────────────────────────────────────


async def test_one_approval_performs_and_records(tmp_path):
    provider = RecordingProvider()
    machine = _machine(tmp_path, provider, PermissionModel())

    async def approve(action, decision):
        return True

    observation = await machine.execute(Action(ActionKind.SCREENSHOT), session="s", approve=approve)
    assert observation.ok
    assert provider.actions == [Action(ActionKind.SCREENSHOT)]


async def test_a_refused_approval_does_not_perform(tmp_path):
    provider = RecordingProvider()
    machine = _machine(tmp_path, provider, PermissionModel())

    async def deny(action, decision):
        return False

    observation = await machine.execute(Action(ActionKind.SCREENSHOT), session="s", approve=deny)
    assert observation.refused
    assert "cancelled" in observation.error
    assert provider.actions == []


async def test_the_destructive_subset_confirms_every_time(tmp_path):
    provider = RecordingProvider()
    machine = _machine(tmp_path, provider, PermissionModel(allowed_apps=["Example"], max_actions=5))
    approvals: list[Action] = []

    async def approve(action, decision):
        approvals.append(action)
        return True

    click = Action(ActionKind.CLICK, target="#go", window="Sign in — Example")
    await machine.execute(click, session="s", approve=approve)
    await machine.execute(click, session="s", approve=approve)
    assert len(approvals) == 2  # a live grant does not amortise a destructive action


async def test_the_grant_bounds_an_action_loop(tmp_path):
    provider = RecordingProvider()
    machine = _machine(tmp_path, provider, PermissionModel(max_actions=2))
    approvals: list[Action] = []

    async def approve(action, decision):
        approvals.append(action)
        return True

    shots = [
        await machine.execute(Action(ActionKind.SCREENSHOT), session="s", approve=approve) for _ in range(4)
    ]
    assert all(s.ok for s in shots)
    assert len(approvals) == 2  # a grant of two, then a fresh approval for the next two


async def test_every_attempt_writes_exactly_one_record(tmp_path):
    provider = RecordingProvider()
    machine = _machine(tmp_path, provider, PermissionModel(max_actions=5))

    async def approve(action, decision):
        return True

    await machine.execute(Action(ActionKind.SCREENSHOT), session="s", approve=approve)
    await machine.execute(Action(ActionKind.SCREENSHOT), session="s", approve=approve)
    await machine.execute(Action(ActionKind.NAVIGATE, target="https://nope.example"), session="s", approve=approve)
    lines = [
        line
        for line in (tmp_path / "config" / "actions.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(lines) == 3
    decisions = [json.loads(line)["decision"] for line in lines]
    assert decisions == ["allowed", "allowed", "refused"]
