"""`iris.channels.brain` — the brain-client contract.

The bridge used to hand-parse SSE and hand-build every core URL; this is the
single definition of that contract, tested against `httpx.MockTransport` so no
network is involved.
"""

from __future__ import annotations

import json

import httpx
import pytest

from iris.channels.brain import BrainEvent, HttpBrainClient, parse_sse_line


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ('data: {"kind": "thinking", "delta": "hmm"}', "thinking"),
        ('data: {"kind": "text", "delta": "Hel"}', "text"),
        ('data: {"kind": "reply", "text": "Hello"}', "reply"),
        ('data: {"kind": "thinking_done", "text": "done"}', "thinking_done"),
        ('data: {"kind": "tool_call", "call": {"name": "memory_search"}}', "tool_call"),
        ('data: {"kind": "approval", "payload": {"action": "forget"}}', "approval"),
        ('data: {"kind": "error"}', "error"),
    ],
)
def test_parse_sse_line_reads_every_event_kind(line: str, kind: str):
    event = parse_sse_line(line)
    assert isinstance(event, BrainEvent)
    assert event.kind == kind


def test_parse_sse_line_carries_the_fields():
    assert parse_sse_line('data: {"kind": "text", "delta": "ab"}').delta == "ab"
    assert parse_sse_line('data: {"kind": "reply", "text": "hi"}').text == "hi"
    assert parse_sse_line('data: {"kind": "tool_call", "call": {"name": "x"}}').call == {"name": "x"}
    assert parse_sse_line('data: {"kind": "approval", "payload": {"a": 1}}').payload == {"a": 1}


@pytest.mark.parametrize(
    "line",
    ["", "event: message", ": ping", "data: {not json", "data: 5", "data: []", "[DONE]", "data: null"],
)
def test_parse_sse_line_never_raises(line: str):
    """A malformed frame is skipped, never a crashed stream."""
    assert parse_sse_line(line) is None


def _client(handler) -> HttpBrainClient:
    return HttpBrainClient(
        "http://core:8000/",
        token="tok",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


_SSE_BODY = (
    'data: {"kind": "thinking", "delta": "\u2026"}\n'
    "\n"
    'data: {"kind": "tool_call", "call": {"name": "memory_search"}}\n'
    "\n"
    'data: {"kind": "text", "delta": "Hi"}\n'
    "\n"
    'data: {"kind": "reply", "text": "Hi there"}\n'
    "\n"
)


async def test_stream_yields_events_in_order_with_auth_and_payload():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=_SSE_BODY.encode())

    kinds = [event.kind async for event in _client(handler).stream("hello", session_id="42")]
    assert kinds == ["thinking", "tool_call", "text", "reply"]
    assert seen["path"] == "/chat/stream"
    assert seen["payload"] == {"message": "hello", "session_id": "42"}
    assert seen["auth"] == "Bearer tok"


async def test_stream_sends_the_image_when_given():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, content=_SSE_BODY.encode())

    async for _ in _client(handler).stream("caption", session_id="1", image="data:image/png;base64,AA"):
        break
    assert seen["payload"]["image"] == "data:image/png;base64,AA"


async def test_respond_posts_the_turn_and_returns_the_reply():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "hello", "onboarded": True})

    reply = await _client(handler).respond("hi", session_id="7")
    assert reply == "hello"
    assert seen["path"] == "/chat"
    assert seen["payload"] == {"message": "hi", "session_id": "7"}


async def test_resume_posts_the_decision():
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"reply": "done", "onboarded": True})

    reply = await _client(handler).resume("7", decision="approved")
    assert reply == "done"
    assert seen["path"] == "/chat/resume"
    assert seen["payload"] == {"session_id": "7", "decision": "approved"}


async def test_json_get_and_post_round_trip():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/mind":
            return httpx.Response(200, json={"memory": "curated"})
        return httpx.Response(200, json={"ok": True})

    client = _client(handler)
    assert await client.json_get("/mind") == {"memory": "curated"}
    assert await client.json_post("/forget", {"query": "tea"}) == {"ok": True}


async def test_injected_client_is_not_closed_by_the_wrapper():
    """The bridge owns one process-wide client; the wrapper must not close it."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as owned:
        client = HttpBrainClient("http://core:8000", client=owned)
        await client.aclose()
        assert not owned.is_closed
