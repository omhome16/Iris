"""The full-screen chat renders replies and approvals from typed events."""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.cli.tui.app import ChatApp
from iris_ai.cli.tui.approval import ApprovalScreen
from iris_ai.kernel.events import TextDelta, ToolEnd, ToolStart, Usage

pytest.importorskip("textual")


class _Files:
    def __init__(self, root: Path) -> None:
        self.root = root


class _Runtime:
    def __init__(self, root: Path) -> None:
        self.files = _Files(root)
        self.dreams = None


class _Threads:
    def __init__(self, brain: FakeBrain) -> None:
        self.brain = brain

    async def pending_approval(self, thread_id: str) -> str | None:
        return self.brain.pending

    async def list_threads(self) -> list[str]:
        return ["alpha"]


class _Graph:
    def __init__(self, brain: FakeBrain) -> None:
        self.threads = _Threads(brain)


class FakeBrain:
    def __init__(self, root: Path, *, approve: bool = False) -> None:
        self.runtime = _Runtime(root)
        self.graph = _Graph(self)
        self.pending: str | None = None
        self.resumed: str | None = None
        self.approve = approve

    async def stream(self, text: str, *, session_id: str, image: str | None = None):
        if self.approve:
            self.pending = "run the command?"
            yield TextDelta(delta="asking")
            return
        yield ToolStart(call={"name": "memory_search", "args": {"query": text}})
        yield ToolEnd(name="memory_search", ok=True, summary="2 hits", result="2 hits", duration_ms=12)
        yield TextDelta(delta="Hello there")
        yield Usage(tokens=4, cost=0.001, model="test")

    async def resume(self, session_id: str, *, decision: str) -> str:
        self.resumed = decision
        self.pending = None
        return "done after approval"


def _lines(app: ChatApp) -> list[str]:
    bits: list[str] = []
    for child in app.query_one("#transcript").children:
        body = getattr(child, "body", None)
        plain = getattr(child, "plain", None)
        bits.append(str(body if body is not None else plain or ""))
    return bits


async def _ready(pilot) -> None:
    for _ in range(40):
        if pilot.app._booted:
            return
        await pilot.pause(0.05)
    raise AssertionError("chat never finished starting")


async def _settled(pilot) -> None:
    for _ in range(40):
        if not pilot.app._busy:
            return
        await pilot.pause(0.05)
    raise AssertionError("turn did not finish")


async def test_chat_renders_reply_and_tool_card(tmp_path: Path):
    app = ChatApp(session="t", brain=FakeBrain(tmp_path))
    async with app.run_test() as pilot:
        await _ready(pilot)
        pilot.app.query_one("#prompt").value = "hi"
        await pilot.press("enter")
        await _settled(pilot)
        lines = _lines(pilot.app)
        assert any("Hello there" in line for line in lines)
        assert any("memory_search" in line and "ok" in line for line in lines)
        assert "4 tok" in str(pilot.app.query_one("#status").render())


async def test_chat_approval_round_trip(tmp_path: Path):
    brain = FakeBrain(tmp_path, approve=True)
    app = ChatApp(session="t", brain=brain)
    async with app.run_test() as pilot:
        await _ready(pilot)
        pilot.app.query_one("#prompt").value = "please approve this"
        await pilot.press("enter")
        for _ in range(40):
            if isinstance(pilot.app.screen, ApprovalScreen):
                break
            await pilot.pause(0.05)
        else:
            raise AssertionError(f"approval screen did not open: {pilot.app.screen}")
        await pilot.click("#allow")
        await _settled(pilot)
        assert brain.resumed == "approved"
        assert any("done after approval" in line for line in _lines(pilot.app))


async def test_new_session_is_a_fresh_id(tmp_path: Path):
    app = ChatApp(session="cli", brain=FakeBrain(tmp_path))
    async with app.run_test() as pilot:
        await _ready(pilot)
        pilot.app.query_one("#prompt").value = "/new"
        await pilot.press("enter")
        await _settled(pilot)
        assert pilot.app.session != "cli"
        assert len(pilot.app.session) == 12


class _Spend:
    ms = 4


class _Handoff:
    refused = ""
    spend = _Spend()


class _Orchestrator:
    def __init__(self) -> None:
        self.roles = {"researcher": object(), "critic": object()}
        self.asked: list[tuple[str, str]] = []

    async def parallel(self, pairs, session_id: str = ""):
        self.asked = list(pairs)
        return [_Handoff() for _pair in pairs]

    def merge(self, handoffs):
        return ("merged from the team", False)


async def test_mcp_command_lists_catalog_presets(tmp_path: Path):
    app = ChatApp(session="t", brain=FakeBrain(tmp_path))
    async with app.run_test() as pilot:
        await _ready(pilot)
        pilot.app.query_one("#prompt").value = "/mcp"
        await pilot.press("enter")
        await _settled(pilot)
        blob = "\n".join(_lines(pilot.app))
        assert "filesystem" in blob
        assert "github" in blob


async def test_team_command_merges_role_cards(tmp_path: Path):
    brain = FakeBrain(tmp_path)
    brain.runtime.orchestrator = _Orchestrator()
    app = ChatApp(session="t", brain=brain)
    async with app.run_test() as pilot:
        await _ready(pilot)
        pilot.app.query_one("#prompt").value = "/team what changed"
        await pilot.press("enter")
        await _settled(pilot)
        blob = "\n".join(_lines(pilot.app))
        assert "merged from the team" in blob
        assert "researcher" in blob
        assert brain.runtime.orchestrator.asked
