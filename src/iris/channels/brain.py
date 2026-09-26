"""The brain-client contract — one definition of how a client talks to the mind.

`iris chat`, the HTTP API and the Telegram bridge all drive the same turn
pipeline. The CLI does it in-process (`iris.harness()`); the bridge does it over
HTTP. Before this module the contract lived in three places at once: the graph
emitted it, `iris.api` forwarded it, and the bridge hand-parsed SSE lines and
hand-built every core URL. Now clients consume `BrainEvent`s and the event
shapes have one home.

**Dependency rule (enforced by `tests/test_brain_client_imports.py`):** this
module imports stdlib + httpx only. The bridge image installs the package with
`pip install --no-deps .`, so pulling in LangGraph, asyncpg or the memory layer
here would silently drag the whole engine into that image.

Unknown event kinds are forwarded rather than rejected, so a newer server cannot
crash an older bridge — kind is a plain string, with the known set in
`KNOWN_KINDS`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol

import httpx

from iris.security import auth_headers

KNOWN_KINDS = (
    "thinking",
    "thinking_done",
    "text",
    "reply",
    "tool_call",
    "approval",
    "error",
)

_SSE_PREFIX = "data: "


@dataclass(slots=True)
class BrainEvent:
    """One streamed event from a turn."""

    kind: str
    delta: str = ""
    text: str = ""
    call: dict | None = None
    payload: dict | None = None


def parse_sse_line(line: str) -> BrainEvent | None:
    """`data: {...}` → `BrainEvent`; anything else → `None` (never raises).

    Non-data lines (`event:`, `: ping`, blank), malformed JSON, and JSON that is
    not an object are all skipped: one bad frame must not kill a stream.
    """
    if not line.startswith(_SSE_PREFIX):
        return None
    try:
        data = json.loads(line[len(_SSE_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    if not isinstance(kind, str) or not kind:
        return None
    return BrainEvent(
        kind=kind,
        delta=str(data.get("delta") or ""),
        text=str(data.get("text") or ""),
        call=data.get("call") if isinstance(data.get("call"), dict) else None,
        payload=data.get("payload") if isinstance(data.get("payload"), dict) else None,
    )


class BrainClient(Protocol):
    """What a client of the brain must provide (structural — no inheritance)."""

    async def respond(self, text: str, *, session_id: str, image: str | None = None) -> str: ...

    def stream(
        self, text: str, *, session_id: str, image: str | None = None
    ) -> AsyncIterator[BrainEvent]: ...

    async def resume(self, session_id: str, *, decision: str) -> str: ...

    async def json_get(self, path: str) -> dict: ...

    async def json_post(self, path: str, payload: dict) -> dict: ...


class HttpBrainClient:
    """`iris-core` over HTTP — the deployed bridge's client.

    An injected `httpx.AsyncClient` is used but never closed by `aclose()`: the
    caller owns that connection pool (the bridge keeps one for its lifetime).
    """

    def __init__(
        self,
        core_url: str,
        *,
        token: str | None = None,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.core_url = core_url.rstrip("/")
        self._token = token or None
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None

    # ── plumbing ─────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        # One definition of the bearer shape, shared with the server-side check
        # (`iris.security`). A client and a server that disagree about the header
        # fail 100% of the time while each looks correct on its own.
        return auth_headers(self._token)

    def _client_or_new(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── turns ────────────────────────────────────────────────────────────
    async def respond(self, text: str, *, session_id: str, image: str | None = None) -> str:
        payload: dict = {"message": text, "session_id": session_id}
        if image:
            payload["image"] = image
        r = await self._client_or_new().post(f"{self.core_url}/chat", json=payload, headers=self._headers())
        r.raise_for_status()
        return str(r.json().get("reply", ""))

    async def resume(self, session_id: str, *, decision: str) -> str:
        r = await self._client_or_new().post(
            f"{self.core_url}/chat/resume",
            json={"session_id": session_id, "decision": decision},
            headers=self._headers(),
        )
        r.raise_for_status()
        return str(r.json().get("reply", ""))

    async def stream(
        self, text: str, *, session_id: str, image: str | None = None
    ) -> AsyncIterator[BrainEvent]:
        """Yield the turn's events as they arrive (`text`, `tool_call`, …)."""
        payload: dict = {"message": text, "session_id": session_id}
        if image:
            payload["image"] = image
        client = self._client_or_new()
        async with client.stream(
            "POST", f"{self.core_url}/chat/stream", json=payload, headers=self._headers()
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                event = parse_sse_line(line)
                if event is not None:
                    yield event

    # ── plain JSON endpoints (commands, reports) ──────────────────────────
    async def json_get(self, path: str) -> dict:
        r = await self._client_or_new().get(f"{self.core_url}{path}", headers=self._headers())
        r.raise_for_status()
        return r.json()

    async def json_post(self, path: str, payload: dict) -> dict:
        r = await self._client_or_new().post(f"{self.core_url}{path}", json=payload, headers=self._headers())
        r.raise_for_status()
        return r.json()
