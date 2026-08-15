"""Tests for the Telegram MCP bridge command dispatcher (pure logic, fake core)."""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "mcp_servers" / "telegram"
sys.path.insert(0, str(BRIDGE_DIR))

from server import CommandDispatcher  # noqa: E402


class FakeCoreTransport(httpx.AsyncBaseTransport):
    """Serves the iris-core endpoints the dispatcher calls, in-memory."""

    def __init__(self, mind=None, retention=None, rot=None, skills=None, forget=None, sleep=None):
        self.mind = mind or {"memory": "M", "user": "U", "dreams_tail": "D", "skills": [], "stats": {}}
        self.retention = retention or {"chunks": [], "curve": []}
        self.rot = rot or {"count": 0, "entries": []}
        self.skills = skills or {"skills": []}
        self.forget = forget or {"candidates": []}
        self.sleep = sleep or {"staged": 0, "promoted": 0, "themes": 0, "added": 0, "superseded": 0}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url, method = str(request.url), request.method
        payload: dict = {}
        if url.endswith("/mind") and method == "GET":
            payload = self.mind
        elif url.endswith("/retention") and method == "GET":
            payload = self.retention
        elif url.endswith("/rot") and method == "GET":
            payload = self.rot
        elif url.endswith("/skills") and method == "GET":
            payload = self.skills
        elif url.endswith("/sleep") and method == "POST":
            payload = self.sleep
        elif url.endswith("/forget") and method == "POST":
            payload = self.forget
        elif url.endswith("/forget/confirm") and method == "POST":
            payload = {"ok": True, "superseded": "old fact"}
        else:
            return httpx.Response(404, request=request)
        return httpx.Response(200, json=payload, request=request)


@pytest.fixture()
def dispatcher() -> CommandDispatcher:
    client = httpx.AsyncClient(transport=FakeCoreTransport())
    d = CommandDispatcher("http://core", client=client)
    yield d


@pytest.mark.anyio
async def test_plain_message_is_not_a_command(dispatcher: CommandDispatcher):
    assert await dispatcher.dispatch(1, "hello there") is None


@pytest.mark.anyio
async def test_unknown_command(dispatcher: CommandDispatcher):
    reply = await dispatcher.dispatch(1, "/frobnicate")
    assert "Unknown command" in reply


@pytest.mark.anyio
async def test_help_lists_commands(dispatcher: CommandDispatcher):
    reply = await dispatcher.dispatch(1, "/help")
    assert "/mind" in reply and "/sleep" in reply and "/forget" in reply


@pytest.mark.anyio
async def test_mind_formats_memory_and_skills(dispatcher: CommandDispatcher):
    dispatcher._client = httpx.AsyncClient(
        transport=FakeCoreTransport(
            mind={
                "memory": "M job",
                "user": "U name",
                "dreams_tail": "D theme",
                "skills": ["skill_a"],
                "stats": {"total_chunks": 42},
            }
        )
    )
    reply = await dispatcher.dispatch(1, "/mind")
    assert "M job" in reply and "U name" in reply and "skill_a" in reply and "42 chunks" in reply


@pytest.mark.anyio
async def test_wake_reports_decay(dispatcher: CommandDispatcher):
    dispatcher._client = httpx.AsyncClient(
        transport=FakeCoreTransport(
            retention={
                "chunks": [
                    {"retention": 0.9},
                    {"retention": 0.3},
                    {"retention": 0.4},
                ],
                "curve": [],
            },
            rot={"count": 2, "entries": [{"content": "x", "retention": 0.1, "age_days": 90}]},
        )
    )
    reply = await dispatcher.dispatch(1, "/wake")
    assert "3 memory chunks" in reply and "2 decaying" in reply and "flagged as rot" in reply


@pytest.mark.anyio
async def test_rot_empty(dispatcher: CommandDispatcher):
    reply = await dispatcher.dispatch(1, "/rot")
    assert "No rot detected" in reply


@pytest.mark.anyio
async def test_forget_requires_argument(dispatcher: CommandDispatcher):
    reply = await dispatcher.dispatch(1, "/forget")
    assert "Usage" in reply


@pytest.mark.anyio
async def test_forget_two_phase_hitl(dispatcher: CommandDispatcher):
    dispatcher._client = httpx.AsyncClient(
        transport=FakeCoreTransport(
            forget={"candidates": [{"content": "old fact", "path": "MEMORY.md", "chunk_index": 3}]}
        )
    )
    first = await dispatcher.dispatch(1, "/forget old fact")
    assert "old fact" in first and "/forget-confirm" in first
    reply = await dispatcher.dispatch(1, "/forget-confirm")
    assert "Retired" in reply and "superseded" in reply
    assert 1 not in dispatcher.pending


@pytest.mark.anyio
async def test_forget_cancel_aborts(dispatcher: CommandDispatcher):
    dispatcher._client = httpx.AsyncClient(
        transport=FakeCoreTransport(
            forget={"candidates": [{"content": "old fact", "path": "MEMORY.md", "chunk_index": 3}]}
        )
    )
    await dispatcher.dispatch(1, "/forget old fact")
    reply = await dispatcher.dispatch(1, "/forget-cancel")
    assert "cancelled" in reply.lower()
    assert 1 not in dispatcher.pending


@pytest.mark.anyio
async def test_forget_confirm_without_pending(dispatcher: CommandDispatcher):
    reply = await dispatcher.dispatch(1, "/forget-confirm")
    assert "No pending forget" in reply


@pytest.mark.anyio
async def test_sleep_reports_dream_record(dispatcher: CommandDispatcher):
    dispatcher._client = httpx.AsyncClient(
        transport=FakeCoreTransport(sleep={"staged": 3, "promoted": 2, "themes": 1, "added": 1, "superseded": 0})
    )
    reply = await dispatcher.dispatch(1, "/sleep")
    assert "staged=3" in reply and "promoted=2" in reply and "added=1" in reply