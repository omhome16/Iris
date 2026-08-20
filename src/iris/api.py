"""Iris API — FastAPI entrypoint.

Boot sequence (lifespan):
1. Connect the memory index (Postgres + pgvector, schema ensured)
2. Reindex the workspace files (files are the source of truth)
3. Build the Runtime + PostgresSaver checkpointer + chat graph
4. (Chapters 5+) start the scheduler + Telegram MCP channel
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

# psycopg async (used by the LangGraph PostgresSaver) cannot run on
# Windows' ProactorEventLoop; select the selector loop before any loop exists.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import Depends, FastAPI, File, Form, UploadFile
from fastapi.responses import StreamingResponse
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel

from iris.agent.chat import ApprovalRequired, ChatGraph
from iris.agent.runtime import Runtime
from iris.channels.telegram_mcp import TelegramMCPClient
from iris.config import settings
from iris.memory.dreaming import DreamEngine
from iris.memory.files import ConcurrencyError, WorkspaceFiles
from iris.memory.forgetting import ForgettingEngine, decay_curve
from iris.memory.index import MemoryIndex
from iris.memory.provenance import Origin
from iris.memory.indexer import Reindexer
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
from iris.onboarding import OnboardingWizard
from iris.sandbox import Sandbox
from iris.trace import TraceLogger
from iris.scheduler import build_scheduler
from iris.security import require_token, warn_if_unset
from iris.voice import transcribe

log = logging.getLogger("iris")


def _checkpointer_dsn(dsn: str) -> str:
    return dsn.replace("postgresql+psycopg://", "postgresql://")


def _sync_owner_chat_id() -> bool:
    """The Telegram bridge learns the owner's chat id at the first /start and
    persists it to data/owner.json. The core previously relied on a copy in
    .env (OWNER_CHAT_ID) that nothing ever updated — so morning briefs and
    scheduled deliveries stayed silent after the bridge discovered the owner.
    Sync the bridge's answer into settings when the env value is unset."""
    if settings.owner_chat_id:
        return True
    env_file = os.environ.get("OWNER_FILE", "")
    candidates: list[Path] = [Path(env_file)] if env_file else []
    candidates += [
        Path(settings.workspace_dir).parent / "mcp_servers" / "telegram" / "data" / "owner.json",
        Path("mcp_servers") / "telegram" / "data" / "owner.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            chat_id = int(json.loads(path.read_text(encoding="utf-8")).get("chat_id"))
        except Exception:  # noqa: BLE001 - best-effort sync
            continue
        if chat_id:
            settings.owner_chat_id = chat_id
            log.info("owner chat id synced from bridge (%s): %s", path, chat_id)
            return True
    return False


def _text_of(content: object) -> str:
    """Text from a message content that may be a plain string or a list of
    content blocks (OpenAI image/text format)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict)
        )
    return ""


@asynccontextmanager
async def lifespan(app: FastAPI):
    files = WorkspaceFiles(Path(settings.workspace_dir))
    _sync_owner_chat_id()

    from iris.ledger import CostLedger

    ledger = CostLedger(files.root / "config" / "llm_calls.jsonl")
    llm = LLMClient(ledger=ledger)
    index = MemoryIndex(settings.postgres_dsn, llm)
    await index.connect()

    reindexer = Reindexer(files, index, llm)
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
            traces=TraceLogger(files.root / "config" / "traces.jsonl"),
        )

        from iris.agent.subagents import ResearchSubagent

        runtime.research = ResearchSubagent(runtime)

        telegram = TelegramMCPClient(settings.telegram_mcp_url)
        if await telegram.connect():
            runtime.telegram = telegram
        else:
            # Bridge may still be starting; retry in the background so the
            # channel appears as soon as it is reachable (no boot dependency).
            # Previously the retry gave up after 10x5s and the channel stayed
            # dead until restart — matching the repeated "telegram MCP
            # unavailable" errors in the logs.
            log.warning("telegram channel not connected; retrying in background")

            async def _retry_telegram() -> None:
                delay = 5.0
                while True:
                    await asyncio.sleep(delay)
                    if await telegram.connect():
                        runtime.telegram = telegram
                        log.info("telegram channel connected on retry")
                        return
                    delay = min(delay * 1.5, 120.0)

            asyncio.create_task(_retry_telegram())

        graph = ChatGraph(runtime, saver)

        scheduler = build_scheduler(runtime)
        scheduler.start()
        log.info("scheduler started: %s", [j.id for j in scheduler.get_jobs()])

        from iris.tasks import TaskScheduler, TaskStore

        task_scheduler = TaskScheduler(
            TaskStore(files.root / "config" / "tasks.json"),
            runtime,
            graph,
            scheduler,
        )
        task_scheduler.register_all()
        runtime.tasks = task_scheduler

        def _on_onboarded() -> None:
            from iris.scheduler import owner_sleep_hour, reschedule_nightly

            reschedule_nightly(scheduler, owner_sleep_hour(files.root))

        runtime.on_onboarded = _on_onboarded

        app.state.runtime = runtime
        app.state.graph = graph
        try:
            yield
        finally:
            scheduler.shutdown(wait=False)
            await telegram.close()
            await index.close()


app = FastAPI(title="Iris", version="0.1.0", lifespan=lifespan)
warn_if_unset()


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    image: str | None = None  # base64 data URI (data:image/...;base64,...)


class ChatResumeRequest(BaseModel):
    session_id: str
    decision: str = "cancelled"


class ChatResponse(BaseModel):
    reply: str
    onboarded: bool
    pending: bool = False
    approval: dict | None = None  # human-in-the-loop payload when pending


@app.get("/health")
async def health() -> dict:
    index: MemoryIndex = app.state.runtime.index
    return {"status": "ok", "service": "iris", "memory": await index.stats()}


@app.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest, _token: None = Depends(require_token)
) -> ChatResponse:
    """One chat turn through the durable graph. Routes to the onboarding
    wizard until identity is born; then the ReAct loop.

    When a tool asks for approval (forget), the turn halts and the response
    carries `pending=True` with the approval payload; resume via
    POST /chat/resume."""
    graph: ChatGraph = app.state.graph
    pending: dict | None = None
    try:
        reply = await asyncio.wait_for(
            graph.respond(req.message, session_id=req.session_id, image=req.image),
            timeout=120.0,
        )
    except ApprovalRequired as exc:
        pending = exc.payload
        reply = "I need your approval before doing that — resume with POST /chat/resume (decision: approved | cancelled)."
    except TimeoutError:
        log.warning("chat turn exceeded 120s budget; returning fallback")
        reply = "I'm still thinking — the language model is under load right now. Give me a minute and say that again."
    except Exception as exc:  # noqa: BLE001 - a turn must never 500; surface it instead
        log.exception("chat turn failed")
        reply = f"I hit an unexpected error ({type(exc).__name__}) — say that again, or try later."
    # wizard state lives on disk; reload for a fresh read (graph may have run it)
    wizard = OnboardingWizard(app.state.runtime.files)
    return ChatResponse(
        reply=reply,
        onboarded=wizard.onboarded,
        pending=pending is not None,
        approval=pending,
    )


@app.post("/chat/resume", response_model=ChatResponse)
async def chat_resume(
    req: ChatResumeRequest, _token: None = Depends(require_token)
) -> ChatResponse:
    """Resume an interrupted turn (human-in-the-loop approval) with the
    owner's decision: "approved" performs the pending action, anything else
    cancels it. The existing two-phase endpoints (/forget, /forget/confirm)
    remain as the fallback path."""
    graph: ChatGraph = app.state.graph
    try:
        reply = await asyncio.wait_for(
            graph.resume(req.session_id, decision=req.decision),
            timeout=120.0,
        )
    except ApprovalRequired as exc:
        return ChatResponse(
            reply="I need your approval before doing that — resume with POST /chat/resume (decision: approved | cancelled).",
            onboarded=True,
            pending=True,
            approval=exc.payload,
        )
    except TimeoutError:
        reply = "I'm still thinking — the language model is under load right now. Give me a minute and say that again."
    except Exception as exc:  # noqa: BLE001
        log.exception("chat resume failed")
        reply = f"I hit an unexpected error ({type(exc).__name__}) — say that again, or try later."
    wizard = OnboardingWizard(app.state.runtime.files)
    return ChatResponse(reply=reply, onboarded=wizard.onboarded)


@app.post("/chat/stream")
async def chat_stream(
    req: ChatRequest, _token: None = Depends(require_token)
) -> StreamingResponse:
    """SSE streaming chat with a visible mind.

    Events (named `message`):
    - {"kind": "thinking", "delta": ...}   reasoning tokens
    - {"kind": "text", "delta": ...}       reply tokens
    - {"kind": "tool_call", "call": {...}} tool invocation
    - {"kind": "reply", "text": ...}       final reply (last event)
    """
    graph: ChatGraph = app.state.graph

    async def gen():
        async for mode, data in graph.respond_stream(
            req.message, session_id=req.session_id, image=req.image
        ):
            if mode == "custom":
                yield f"data: {json.dumps(data)}\n\n"
            elif mode == "error":
                yield f"data: {json.dumps({'kind': 'reply', 'text': data})}\n\n"
            elif mode == "updates":
                for node, update in (data or {}).items():
                    for m in (update or {}).get("messages", []):
                        mtype = m.get("type") if isinstance(m, dict) else getattr(m, "type", "")
                        mcalls = m.get("tool_calls") if isinstance(m, dict) else getattr(m, "tool_calls", None)
                        mcontent = m.get("content") if isinstance(m, dict) else m.content
                        if mtype == "ai" and not mcalls:
                            yield f"data: {json.dumps({'kind': 'reply', 'text': _text_of(mcontent)})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/voice")
async def voice_turn(
    user_id: str = Form(...),
    audio: UploadFile = File(...),
    _token: None = Depends(require_token),
) -> dict:
    """Voice note → Groq Whisper transcript → the same chat graph as /chat.

    Called by the Telegram bridge, which downloads the voice message and
    forwards the audio file here. Replies with the graph's reply text.
    """
    import tempfile

    graph: ChatGraph = app.state.graph
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".ogg")
    tmp_path = Path(tmp.name)
    try:
        tmp.write(await audio.read())
        tmp.close()
        transcript = await transcribe(tmp_path)
    except Exception as exc:  # noqa: BLE001 - a voice turn must never 500
        log.warning("voice turn failed: %s", exc)
        return {"reply": f"I couldn't hear you: {exc}"}
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        reply = await asyncio.wait_for(
            graph.respond(transcript, session_id=user_id), timeout=120.0
        )
    except ApprovalRequired as exc:
        reply = f"I need your approval before doing that ({exc.payload.get('action', 'action')}). Say it again to try once more."
    except TimeoutError:
        reply = "I'm still thinking — the language model is under load. Say that again in a bit."
    except Exception as exc:  # noqa: BLE001
        log.exception("voice graph turn failed")
        reply = f"I hit an unexpected error ({type(exc).__name__}) — try again later."
    return {"reply": reply, "transcript": transcript}


@app.get("/onboarding")
async def onboarding_status(_token: None = Depends(require_token)) -> dict:
    wizard = OnboardingWizard(app.state.runtime.files)
    return {
        "onboarded": wizard.onboarded,
        "step": wizard.state.step,
        "next_prompt": wizard.current_prompt(),
    }


@app.post("/sleep")
async def sleep(_token: None = Depends(require_token)) -> dict:
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
async def rot(_token: None = Depends(require_token)) -> dict:
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
async def retention(_token: None = Depends(require_token)) -> dict:
    """Per-chunk retention stats + decay curve — dashboard feed."""
    forgetting: ForgettingEngine = app.state.runtime.forgetting
    rows = await forgetting.retention_report()
    curve = decay_curve()
    return {"chunks": rows, "curve": curve}


@app.get("/mind")
async def mind(_token: None = Depends(require_token)) -> dict:
    """Full memory snapshot — what Iris remembers, her dreams and skills.
    Feeds the /mind command on Telegram."""
    runtime: Runtime = app.state.runtime
    files = runtime.files
    dreams = files.read(files.dreams)[-2000:] if files.dreams.exists() else ""
    skills = [{"name": s.name, "description": s.description} for s in runtime.skills.list()]
    stats = await runtime.index.stats()
    from iris.memory.reflection import ReflectionPass

    flags = ReflectionPass(runtime.llm, files.root / "config" / "hallucination_flags.jsonl").count()
    return {
        "memory": files.read(files.memory),
        "user": files.read(files.user),
        "dreams_tail": dreams,
        "skills": skills,
        "stats": stats,
        "hallucination_flags": flags,
    }


@app.get("/skills")
async def skills_list(_token: None = Depends(require_token)) -> dict:
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


@app.get("/tasks")
async def tasks_list(_token: None = Depends(require_token)) -> dict:
    """Pending one-off scheduled tasks (dashboard + /tasks feed)."""
    runtime: Runtime = app.state.runtime
    tasks = runtime.tasks.pending() if runtime.tasks is not None else []
    return {"tasks": [t.to_dict() for t in tasks]}


@app.get("/costs")
async def costs(_token: None = Depends(require_token)) -> dict:
    """LLM spend rollups from the append-only cost ledger."""
    runtime: Runtime = app.state.runtime
    ledger = runtime.llm.ledger
    if ledger is None:
        return {"totals": {"requests": 0, "cost": 0.0}, "daily": [], "weekly": []}
    return {
        "totals": ledger.totals(),
        "daily": ledger.daily_totals(),
        "weekly": ledger.weekly_totals(),
    }


@app.get("/traces")
async def traces(limit: int = 20, _token: None = Depends(require_token)) -> dict:
    """Recent turn traces (config/traces.jsonl, newest first)."""
    logger = app.state.runtime.traces
    return {"traces": logger.recent(max(1, min(limit, 100))) if logger else []}


class ForgetRequest(BaseModel):
    query: str


class ForgetConfirmRequest(BaseModel):
    path: str
    chunk_index: int


@app.post("/forget")
async def forget_search(
    req: ForgetRequest, _token: None = Depends(require_token)
) -> dict:
    """HITL phase 1: find candidate memories matching the query.

    Restricted to MEMORY.md: the confirm step only edits curated owner
    memory, so returning daily-note candidates was a dead end."""
    runtime: Runtime = app.state.runtime
    hits = await runtime.index.search(
        req.query, top_k=3, mrr_top_k=1, require_origin={Origin.OWNER}
    )
    hits = [h for h in hits if h.path == "MEMORY.md"]
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
async def forget_confirm(
    req: ForgetConfirmRequest, _token: None = Depends(require_token)
) -> dict:
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
    # Indexed chunks may carry contextual-retrieval headers, so an exact
    # replace can miss even when the fact is present in the raw file —
    # fall back to a line-level match before giving up.
    new = content.replace(chunk["content"], f"{chunk['content']} {marker}")
    if new == content:
        probe = str(chunk["content"]).strip().splitlines()[0][:120]
        target_line = next(
            (line for line in content.splitlines() if probe in line), None
        )
        if target_line is None:
            return {"ok": False, "error": "could not locate the entry text in MEMORY.md"}
        new = content.replace(target_line, f"{target_line} {marker}")
    if new == content:
        return {"ok": False, "error": "could not locate the entry text in MEMORY.md"}
    try:
        files.write_curated(files.memory, new)
        await runtime.reindexer.reindex_all()
    except ConcurrencyError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "superseded": chunk["content"][:120]}