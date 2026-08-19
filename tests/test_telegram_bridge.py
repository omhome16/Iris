"""Tests for the Telegram MCP bridge command dispatcher (pure logic, fake core)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import anyio
import httpx
import pytest

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "mcp_servers" / "telegram"
sys.path.insert(0, str(BRIDGE_DIR))

import server  # noqa: E402
from server import CommandDispatcher  # noqa: E402


class FakeCoreTransport(httpx.AsyncBaseTransport):
    """Serves the iris-core endpoints the dispatcher calls, in-memory."""

    def __init__(self, mind=None, retention=None, rot=None, skills=None, forget=None, sleep=None, tasks=None):
        self.mind = mind or {"memory": "M", "user": "U", "dreams_tail": "D", "skills": [], "stats": {}}
        self.retention = retention or {"chunks": [], "curve": []}
        self.rot = rot or {"count": 0, "entries": []}
        self.skills = skills or {"skills": []}
        self.forget = forget or {"candidates": []}
        self.sleep = sleep or {"staged": 0, "promoted": 0, "themes": 0, "added": 0, "superseded": 0}
        self.tasks = tasks or {"tasks": []}

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
        elif url.endswith("/tasks") and method == "GET":
            payload = self.tasks
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


class FakeBridgeTransport(httpx.AsyncBaseTransport):
    """Serves the Telegram file download + iris-core /voice, in-memory."""

    def __init__(self):
        self.voice_posts: list[dict] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(f"{server.BOT_API}/"):
            return httpx.Response(200, content=b"fake audio bytes", request=request)
        if str(request.url).endswith("/voice"):
            self.voice_posts.append(dict(request.headers))
            return httpx.Response(
                200,
                json={"reply": "heard you", "transcript": "the owner said hi"},
                request=request,
            )
        return httpx.Response(404, request=request)


@pytest.mark.anyio
async def test_handle_voice_uses_bot_token(monkeypatch: pytest.MonkeyPatch):
    """Regression: _handle_voice must not raise NameError (undefined
    TELEGRAM_BOT_TOKEN); the download URL uses the BOT_API constant."""
    transport = FakeBridgeTransport()

    async def fake_tg(method: str, **params) -> dict:
        assert method == "getFile"
        return {"file_path": "audio/file_1.ogg"}

    sent: list[str] = []

    async def fake_send(chat_id: int, text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(server, "_tg", fake_tg)
    monkeypatch.setattr(server, "send_to_chat", fake_send)
    real_client = server.httpx.AsyncClient
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: real_client(transport=transport)
    )

    await server._handle_voice(123, {"file_id": "file_1"})
    assert sent[-1] == "heard you"
    assert transport.voice_posts, "/voice must be called on iris-core"


@pytest.mark.anyio
async def test_typing_loop_sends_chat_action(monkeypatch: pytest.MonkeyPatch):
    calls: list[str] = []

    async def fake_tg(method: str, **kwargs):
        calls.append(method)
        if len(calls) >= 2:
            raise asyncio.CancelledError  # stop the loop after proving it repeats

    async def no_sleep(_seconds: float):
        return None

    monkeypatch.setattr(server, "_tg", fake_tg)
    monkeypatch.setattr(server.asyncio, "sleep", no_sleep)
    task = asyncio.create_task(server._typing_loop(123))
    await anyio.sleep(0.1)  # real clock — the loop's patched sleep runs instantly
    with pytest.raises(asyncio.CancelledError):
        await task
    assert calls == ["sendChatAction", "sendChatAction"]


@pytest.mark.anyio
async def test_typing_loop_ignores_errors(monkeypatch: pytest.MonkeyPatch):
    async def fake_tg(method: str, **kwargs):
        raise RuntimeError("telegram hiccup")

    monkeypatch.setattr(server, "_tg", fake_tg)
    task = asyncio.create_task(server._typing_loop(123))
    await asyncio.sleep(0.2)
    assert task.done()  # cosmetic loop dies silently, never crashes the poller


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
async def test_tasks_command_lists_pending(dispatcher: CommandDispatcher):
    dispatcher._client = httpx.AsyncClient(
        transport=FakeCoreTransport(
            tasks={
                "tasks": [
                    {
                        "id": "abc123",
                        "run_at": "2026-08-22T09:00:00+06:00",
                        "instruction": "water the plants",
                    }
                ]
            }
        )
    )
    reply = await dispatcher.dispatch(1, "/tasks")
    assert "1 scheduled task" in reply
    assert "2026-08-22" in reply
    assert "water the plants" in reply


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