"""Iris dashboard — a black & white, minimal, professional control room.

Serves the single-page UI and proxies every read/write to iris-core:
  GET  /            the dashboard itself
  POST /api/chat    one turn through the agent graph
  GET  /api/mind    memory snapshot (MEMORY.md, USER.md, DREAMS.md, skills)
  GET  /api/retention  per-chunk retention + decay curve
  GET  /api/rot     decayed memories
  GET  /api/skills  procedural memory
  GET  /api/health  index stats
  POST /api/sleep   run the dream cycle
  POST /api/forget  two-phase HITL retire
"""

from __future__ import annotations

import logging
import os

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

log = logging.getLogger("iris.dashboard")

IRIS_CORE_URL = os.environ.get("IRIS_API_URL", "http://127.0.0.1:8000").rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))

app = FastAPI(title="Iris Dashboard")
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))


async def _proxy(path: str, method: str = "GET", json: dict | None = None) -> dict | str:
    async with httpx.AsyncClient(timeout=300) as client:
        r = await client.request(method, f"{IRIS_CORE_URL}{path}", json=json)
        if r.status_code != 200:
            return {"error": f"iris-core {path}: HTTP {r.status_code} {r.text[:200]}"}
        try:
            return r.json()
        except Exception:  # noqa: BLE001
            return r.text


@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"core_url": IRIS_CORE_URL})


@app.post("/api/chat")
async def api_chat(request: Request) -> JSONResponse:
    body = await request.json()
    return JSONResponse(await _proxy("/chat", "POST", body))


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


@app.get("/api/health")
async def api_health() -> JSONResponse:
    return JSONResponse(await _proxy("/health"))


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