"""Bearer-token auth tests: iris-core enforcement + dashboard/bridge forwarding.

iris-core routes are tested by introspection (no lifespan, no Postgres):
every APIRoute except /health must declare the require_token dependency.
Forwarding is tested with in-memory httpx transports.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
from fastapi.routing import APIRoute

from iris.api import app
from iris.config import settings
from iris.security import require_token

BRIDGE_DIR = Path(__file__).resolve().parents[1] / "mcp_servers" / "telegram"
sys.path.insert(0, str(BRIDGE_DIR))

import server  # noqa: E402

PROTECTED = {"/chat", "/voice", "/onboarding", "/sleep", "/rot", "/retention",
             "/mind", "/skills", "/forget", "/forget/confirm", "/tasks", "/costs"}


def _route_dependencies(route: APIRoute) -> set:
    out = set()
    for dep in route.dependant.dependencies:
        if dep.call is not None:
            out.add(dep.call)
    return out


def test_all_routes_except_health_require_token():
    protected = set()
    for route in app.routes:
        if isinstance(route, APIRoute):
            if "/health" in route.path:
                assert require_token not in _route_dependencies(route)
            else:
                assert require_token in _route_dependencies(route), f"{route.path} missing auth"
                protected.add(route.path)
    assert PROTECTED.issubset(protected), PROTECTED - protected


def test_require_token_passes_when_disabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "iris_api_token", "")
    require_token(type("Req", (), {"headers": {}})())


def test_require_token_rejects_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "iris_api_token", "secret-1")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        require_token(type("Req", (), {"headers": {}})())
    assert exc.value.status_code == 401


def test_require_token_rejects_wrong(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "iris_api_token", "secret-1")
    from fastapi import HTTPException

    with pytest.raises(HTTPException):
        require_token(type("Req", (), {"headers": {"authorization": "Bearer nope"}})())


def test_require_token_accepts_correct(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "iris_api_token", "secret-1")
    require_token(type("Req", (), {"headers": {"authorization": "Bearer secret-1"}})())


# ── forwarding ───────────────────────────────────────────────────────────────

class HeaderProbeTransport(httpx.AsyncBaseTransport):
    def __init__(self):
        self.seen: list[tuple[str, dict]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.seen.append((str(request.url), dict(request.headers)))
        return httpx.Response(200, json={}, request=request)


async def test_bridge_forwards_bearer_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "IRIS_API_TOKEN", "bridge-token-9")
    probe = HeaderProbeTransport()
    client = httpx.AsyncClient(transport=probe)
    d = server.CommandDispatcher("http://core", client=client)
    await d.dispatch(1, "/mind")
    _, headers = probe.seen[0]
    assert headers.get("authorization") == "Bearer bridge-token-9"


async def test_bridge_voice_post_forwards_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "IRIS_API_TOKEN", "voice-token-7")
    probe = HeaderProbeTransport()

    async def fake_tg(method: str, **params) -> dict:
        return {"file_path": "audio/file.ogg"}

    monkeypatch.setattr(server, "_tg", fake_tg)
    real_client = server.httpx.AsyncClient
    monkeypatch.setattr(
        server.httpx, "AsyncClient", lambda *a, **k: real_client(transport=probe)
    )
    await server._handle_voice(1, {"file_id": "f"})
    _, headers = next(
        (u, h) for u, h in probe.seen if str(u).endswith("/voice")
    )
    assert headers.get("authorization") == "Bearer voice-token-7"


async def test_dashboard_proxy_forwards_token(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("dashboard.app.IRIS_API_TOKEN", "dash-token-3")
    from dashboard.app import _proxy

    probe = HeaderProbeTransport()
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "dashboard.app.httpx.AsyncClient", lambda *a, **k: real_client(transport=probe)
    )
    await _proxy("/mind")
    _, headers = probe.seen[0]
    assert headers.get("authorization") == "Bearer dash-token-3"
