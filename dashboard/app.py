"""Iris dashboard — a dawn-sky canvas where the whole mind is on screen at once.

The visual language is a sky with drifting cloud strata; the content is every
tier of the memory system at the same time, because the product claim is
"memory you can see and trust" and a panel you have to open is a panel you
cannot trust at a glance.

Serves the single-page UI and proxies every read/write to iris-core:
  GET  /            the dashboard itself
  POST /api/chat    one turn through the agent graph
  GET  /api/mind    memory snapshot (MEMORY.md, USER.md, DREAMS.md, skills)
  GET  /api/retention  per-chunk retention + decay curve
  GET  /api/rot     decayed memories
  GET  /api/skills  procedural memory
  GET  /api/tasks   scheduled tasks
  GET  /api/costs   LLM spend + cache hit rate
  GET  /api/traces  turn traces, including the judgment layer's decisions
  GET  /api/health  index stats + judgment-layer status
  GET  /api/jev     judgment-layer detail (authenticated on core)
  POST /api/sleep   run the dream cycle
  POST /api/forget  two-phase HITL retire

Auth: the dashboard is a browser app, so it uses HTTP Basic instead of a bearer
header (the browser then sends the credentials on every fetch automatically).
Set DASHBOARD_USER + DASHBOARD_PASSWORD to enable it — without them the app is
open and logs a warning. This matters because the dashboard proxies *write*
routes: an unauthenticated dashboard is an unauthenticated `/forget/confirm`.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("iris.dashboard")

IRIS_CORE_URL = os.environ.get("IRIS_API_URL", "http://127.0.0.1:8000").rstrip("/")
IRIS_API_TOKEN = os.environ.get("IRIS_API_TOKEN", "")
DASHBOARD_USER = os.environ.get("DASHBOARD_USER", "")
DASHBOARD_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "")
HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Iris Dashboard")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """HTTP Basic gate. Enabled only when both env vars are set.

    `/healthz` is exempt so a container healthcheck needs no credentials —
    it returns liveness only, never memory content.
    """
    if not (DASHBOARD_USER and DASHBOARD_PASSWORD) or request.url.path == "/healthz":
        return await call_next(request)
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            decoded = base64.b64decode(header[6:]).decode("utf-8")
            user, _, password = decoded.partition(":")
        except Exception:  # noqa: BLE001 - malformed header is just a 401
            user = password = ""
        if secrets.compare_digest(user, DASHBOARD_USER) and secrets.compare_digest(
            password, DASHBOARD_PASSWORD
        ):
            return await call_next(request)
    return PlainTextResponse(
        "authentication required",
        status_code=401,
        headers={"WWW-Authenticate": 'Basic realm="Iris dashboard"'},
    )


if not (DASHBOARD_USER and DASHBOARD_PASSWORD):
    log.warning(
        "DASHBOARD_USER/DASHBOARD_PASSWORD are not set — the dashboard is "
        "unauthenticated and its write routes (chat, /sleep, /forget) are open."
    )


def _core_headers() -> dict[str, str]:
    if IRIS_API_TOKEN:
        return {"Authorization": f"Bearer {IRIS_API_TOKEN}"}
    return {}


async def _proxy(path: str, method: str = "GET", json: dict | None = None) -> dict | str:
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.request(method, f"{IRIS_CORE_URL}{path}", json=json, headers=_core_headers())
        if r.status_code != 200:
            return {"error": f"iris-core {path}: HTTP {r.status_code} {r.text[:200]}"}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return r.text


@app.get("/healthz")
async def healthz() -> PlainTextResponse:
    """Liveness only — reachable without credentials for container checks."""
    return PlainTextResponse("ok")


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"core_url": IRIS_CORE_URL})


@app.post("/api/chat")
async def api_chat(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(await _proxy("/chat", "POST", body))


@app.post("/api/chat/stream")
async def api_chat_stream(request: Request):
    """Proxy the iris-core SSE stream through the dashboard (one hop)."""
    body = await request.json()
    client = httpx.AsyncClient(timeout=300)
    req = client.build_request("POST", f"{IRIS_CORE_URL}/chat/stream", json=body, headers=_core_headers())
    try:
        r = await client.send(req, stream=True)
    except Exception as exc:  # noqa: BLE001 - a dead core must be a clean SSE error, not a 500
        await client.aclose()
        log.warning("core stream unreachable: %s", exc)
        return StreamingResponse(
            iter([f'data: {json.dumps({"kind": "reply", "text": "iris-core is unreachable right now."})}\n\n']),
            media_type="text/event-stream",
        )

    async def gen():
        try:
            async for line in r.aiter_lines():
                yield line + "\n"
        finally:
            await client.aclose()

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/mind")
async def api_mind() -> JSONResponse:
    return JSONResponse(await _proxy("/mind"))


@app.get("/api/retention")
async def api_retention() -> JSONResponse:
    return JSONResponse(await _proxy("/retention"))


@app.get("/api/rot")
async def api_rot() -> JSONResponse:
    return JSONResponse(await _proxy("/rot"))


@app.get("/api/skills")
async def api_skills() -> JSONResponse:
    return JSONResponse(await _proxy("/skills"))


@app.get("/api/tasks")
async def api_tasks() -> JSONResponse:
    return JSONResponse(await _proxy("/tasks"))


@app.get("/api/costs")
async def api_costs() -> JSONResponse:
    return JSONResponse(await _proxy("/costs"))


@app.get("/api/traces")
async def api_traces() -> JSONResponse:
    return JSONResponse(await _proxy("/traces"))


@app.get("/api/health")
async def api_health() -> JSONResponse:
    return JSONResponse(await _proxy("/health"))


@app.get("/api/jev")
async def api_jev() -> JSONResponse:
    """Judgment-layer health: is JEV live, why not, and how it has behaved."""
    return JSONResponse(await _proxy("/jev"))


@app.post("/api/sleep")
async def api_sleep() -> JSONResponse:
    return JSONResponse(await _proxy("/sleep", "POST"))


@app.post("/api/forget")
async def api_forget(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(await _proxy("/forget", "POST", body))


@app.post("/api/forget/confirm")
async def api_forget_confirm(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(await _proxy("/forget/confirm", "POST", body))
