"""Iris API — FastAPI entrypoint.

Boot sequence (lifespan):
1. Connect the memory index (Postgres + pgvector, schema ensured)
2. Reindex the workspace files (files are the source of truth)
3. Build the Runtime + PostgresSaver checkpointer + chat graph
4. (Chapters 5+) start the scheduler + Telegram MCP channel
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

# psycopg async (used by the LangGraph PostgresSaver) cannot run on
# Windows' ProactorEventLoop; select the selector loop before any loop exists.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import FastAPI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel

from iris.agent.chat import ChatGraph
from iris.agent.runtime import Runtime
from iris.channels.telegram_mcp import TelegramMCPClient
from iris.config import settings
from iris.memory.dreaming import DreamEngine
from iris.memory.files import ConcurrencyError, WorkspaceFiles
from iris.memory.forgetting import ForgettingEngine, decay_curve
from iris.memory.index import MemoryIndex
from iris.memory.indexer import Reindexer
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
from iris.onboarding import OnboardingWizard
from iris.sandbox import Sandbox

log = logging.getLogger("iris")


def _checkpointer_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://")


@asynccontextmanager
async def lifespan(app: FastAPI):
    files = WorkspaceFiles(Path(settings.workspace_dir))
    llm = LLMClient()
    index = MemoryIndex(settings.postgres_dsn, llm)
    await index.connect()

    reindexer = Reindexer(files, index)
    try:
        n = await reindexer.reindex_all()
        log.info("memory index ready: %d chunks reindexed", n)
    except Exception as exc:  # noqa: BLE001 - boot must not die on missing credentials
        log.warning("reindex skipped at boot: %s", exc)

    checkpointer = AsyncPostgresSaver.from_conn_string(
        _checkpointer_dsn(settings.postgres_dsn)
    )
    async with checkpointer as saver:
        await saver.setup()

        runtime = Runtime(
            files=files,
            llm=llm,
            index=index,
            reindexer=reindexer,
            dreams=DreamEngine(llm, files, index),
            forgetting=ForgettingEngine(index),
            skills=SkillLibrary(files),
            sandbox=Sandbox(Path(settings.sandbox_dir)),
        )

        telegram = TelegramMCPClient(settings.telegram_mcp_url)
        if await telegram.connect():
            runtime.telegram = telegram
        else:
            # Bridge may still be starting; retry in the background so the
            # channel appears as soon as it is reachable (no boot dependency).
            log.warning("telegram channel not connected; retrying in background")

            async def _retry_telegram() -> None:
                for _ in range(10):
                    await asyncio.sleep(5)
                    if await telegram.connect():
                        runtime.telegram = telegram
                        log.info("telegram channel connected on retry")
                        return

            asyncio.create_task(_retry_telegram())

        graph = ChatGraph(runtime, saver)

        app.state.runtime = runtime
        app.state.graph = graph
        try:
            yield
        finally:
            await telegram.close()
            await index.close()


app = FastAPI(title="Iris", version="0.1.0", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ChatResponse(BaseModel):
    reply: str
    onboarded: bool


@app.get("/health")
async def health() -> dict:
    index: MemoryIndex = app.state.runtime.index
    return {"status": "ok", "service": "iris", "memory": await index.stats()}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """One chat turn through the durable graph. Routes to the onboarding
    wizard until identity is born; then the ReAct loop."""
    graph: ChatGraph = app.state.graph
    try:
        reply = await asyncio.wait_for(graph.respond(req.message, session_id=req.session_id), timeout=120.0)
    except TimeoutError:
        log.warning("chat turn exceeded 120s budget; returning fallback")
        reply = "I'm still thinking — the language model is under load right now. Give me a minute and say that again."
    except Exception as exc:  # noqa: BLE001 - a turn must never 500; surface it instead
        log.exception("chat turn failed")
        reply = f"I hit an unexpected error ({type(exc).__name__}) — say that again, or try later."
    # wizard state lives on disk; reload for a fresh read (graph may have run it)
    wizard = OnboardingWizard(app.state.runtime.files)
    return ChatResponse(reply=reply, onboarded=wizard.onboarded)


@app.get("/onboarding")
async def onboarding_status() -> dict:
    wizard = OnboardingWizard(app.state.runtime.files)
    return {
        "onboarded": wizard.onboarded,
        "step": wizard.state.step,
        "next_prompt": wizard.current_prompt(),
    }


@app.post("/sleep")
async def sleep() -> dict:
    """Run the dream graph: Light → REM → Deep → DREAMS.md, then reindex."""
    runtime: Runtime = app.state.runtime
    record = await runtime.dreams.sleep()
    try:
        n = await runtime.reindexer.reindex_all()
    except Exception as exc:  # noqa: BLE001
        n = 0
        log.warning("reindex after sleep failed: %s", exc)
    return {
        "staged": record.staged,
        "promoted": record.promoted,
        "themes": len(record.themes),
        "added": record.added,
        "superseded": record.superseded,
        "fallback": record.fallback,
        "reindexed": n,
    }


@app.get("/rot")
async def rot() -> dict:
    """Memory rot report — decayed entries the owner may want to /forget."""
    forgetting: ForgettingEngine = app.state.runtime.forgetting
    entries = await forgetting.rot_report()
    return {
        "count": len(entries),
        "entries": [
            {
                "path": e.path,
                "chunk_index": e.chunk_index,
                "content": e.content,
                "retention": round(e.retention, 3),
                "age_days": e.age_days,
                "reason": e.reason,
            }
            for e in entries
        ],
    }


@app.get("/retention")
async def retention() -> dict:
    """Per-chunk retention stats + decay curve — dashboard feed."""
    forgetting: ForgettingEngine = app.state.runtime.forgetting
    rows = await forgetting.retention_report()
    curve = decay_curve()
    return {"chunks": rows, "curve": curve}


@app.get("/mind")
async def mind() -> dict:
    """Full memory snapshot — what Iris remembers, her dreams and skills.
    Feeds the /mind command on Telegram."""
    runtime: Runtime = app.state.runtime
    files = runtime.files
    dreams = files.read(files.dreams)[-2000:] if files.dreams.exists() else ""
    skills = [{"name": s.name, "description": s.description} for s in runtime.skills.list()]
    stats = await runtime.index.stats()
    return {
        "memory": files.read(files.memory),
        "user": files.read(files.user),
        "dreams_tail": dreams,
        "skills": skills,
        "stats": stats,
    }


@app.get("/skills")
async def skills_list() -> dict:
    runtime: Runtime = app.state.runtime
    return {
        "skills": [
            {
                "name": s.name,
                "description": s.description,
                "triggers": s.triggers,
                "success": s.success_score,
            }
            for s in runtime.skills.list()
        ]
    }


class ForgetRequest(BaseModel):
    query: str


class ForgetConfirmRequest(BaseModel):
    path: str
    chunk_index: int


@app.post("/forget")
async def forget_search(req: ForgetRequest) -> dict:
    """HITL phase 1: find candidate memories matching the query."""
    runtime: Runtime = app.state.runtime
    hits = await runtime.index.search(req.query, top_k=3, mrr_top_k=1)
    return {
        "candidates": [
            {
                "content": h.content,
                "path": h.path,
                "chunk_index": h.chunk_index,
                "score": round(h.score, 3),
                "origin": h.origin.value,
            }
            for h in hits
        ]
    }


@app.post("/forget/confirm")
async def forget_confirm(req: ForgetConfirmRequest) -> dict:
    """HITL phase 2: retire the entry. Supersession marker in the file
    (source of truth), chunk dropped from the index, reindex."""
    runtime: Runtime = app.state.runtime
    files = runtime.files
    if Path(req.path).name != files.memory.name:
        return {"ok": False, "error": "only MEMORY.md entries are editable; daily notes are append-only"}
    content = files.read(files.memory)
    chunks = await runtime.index.list_chunks()
    chunk = next(
        (c for c in chunks if c["path"] == req.path and c["chunk_index"] == req.chunk_index),
        None,
    )
    if chunk is None:
        return {"ok": False, "error": "chunk not found in index"}
    marker = f"(superseded {datetime.now().isoformat()[:10]})"
    new = content.replace(chunk["content"], f"{chunk['content']} {marker}")
    if new == content:
        return {"ok": False, "error": "could not locate the entry text in MEMORY.md"}
    try:
        files.write_curated(files.memory, new)
        await runtime.reindexer.reindex_all()
    except ConcurrencyError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "superseded": chunk["content"][:120]}