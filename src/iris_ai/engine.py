"""The engine's boot path — public as `iris_ai.harness()`.

Why this module is `iris_ai.engine` and not `iris_ai.harness`: a submodule and a
package attribute cannot share a name. Importing `iris_ai.harness` would set the
package attribute to the *module* and silently clobber the `harness()` callable
`iris_ai.harness()` promises, so the implementation lives here and the public name
is re-exported by `iris/__init__.py`.

Before P2 this wiring lived inside `iris_ai.api`'s FastAPI lifespan, which meant
the product's mind could only be started by starting a web framework. The
library is the mind; the HTTP API, the CLI and (P3) the Telegram bridge are
clients of it. So the sequence lives here:

    workspace files → cost ledger → LLM client → JEV → memory index
      → reindex → LangGraph checkpointer → Runtime → ChatGraph
      → (services) scheduler + task scheduler + Telegram channel

Every client opens a `Harness` the same way. The CLI is the reason for the
degraded mode: when no Postgres is reachable, `postgres="auto"` keeps the
conversation alive with an in-memory checkpointer and no vector recall, and
says so — `mode`, `degraded_reason`, and a recall error that names the fix.
The API keeps `postgres="require"`, so a missing database still fails at boot
exactly as it always has.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from iris_ai import background
from iris_ai.agent.chat import ChatGraph
from iris_ai.agent.runtime import Runtime
from iris_ai.agent.tools import TOOL_NAMES
from iris_ai.channels.telegram_mcp import TelegramMCPClient
from iris_ai.config import settings
from iris_ai.jev.client import JevClient
from iris_ai.jev.recall import JevReranker
from iris_ai.ledger import CostLedger
from iris_ai.memory.dreaming import DreamEngine
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.forgetting import ForgettingEngine
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.indexer import Reindexer
from iris_ai.memory.llm import LLMClient
from iris_ai.memory.null_index import NullIndex
from iris_ai.sandbox import Sandbox
from iris_ai.scheduler import build_scheduler, owner_sleep_hour, reschedule_nightly
from iris_ai.skills.registry import open_registry
from iris_ai.tasks import TaskScheduler, TaskStore
from iris_ai.toolpolicy import parse_overrides
from iris_ai.trace import TraceLogger

log = logging.getLogger("iris_ai.harness")

PostgresMode = Literal["require", "auto"]
RunMode = Literal["full", "degraded"]


def _checkpointer_dsn(dsn: str) -> str:
    """LangGraph's PostgresSaver wants a plain psycopg URL, not SQLAlchemy."""
    return dsn.replace("postgresql+psycopg://", "postgresql://")


def sync_owner_chat_id() -> bool:
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


@dataclass(slots=True)
class Harness:
    """A booted engine. Clients call the turn API; nothing else is required.

    `mode` is not decoration: a degraded session cannot recall anything, and
    every caller that can tell the owner must say so.
    """

    files: WorkspaceFiles
    llm: LLMClient
    ledger: CostLedger
    jev: JevClient
    index: MemoryIndex | NullIndex
    runtime: Runtime
    graph: ChatGraph
    mode: RunMode = "full"
    degraded_reason: str | None = None
    telegram: TelegramMCPClient | None = None
    scheduler: object | None = None
    _retry_task: asyncio.Task | None = field(default=None, repr=False)

    # ── turn API (one hot path for CLI, API and bridge) ──────────────────
    async def respond(self, message: str, *, session_id: str = "default", image: str | None = None) -> str:
        """One turn: returns the reply, raises `ApprovalRequired` if paused."""
        return await self.graph.respond(message, session_id=session_id, image=image)

    async def resume(self, session_id: str, *, decision: str) -> str:
        """Finish an interrupted turn with the owner's decision."""
        return await self.graph.resume(session_id, decision=decision)

    def stream(
        self, message: str, *, session_id: str = "default", image: str | None = None
    ) -> AsyncIterator[tuple[str, object]]:
        """Streamed turn: yields `(kind, payload)` from the chat graph."""
        return self.graph.respond_stream(message, session_id=session_id, image=image)

    # ── shutdown ─────────────────────────────────────────────────────────
    async def aclose(self) -> None:
        """Stop everything this harness started, in dependency order.

        Same order the API's shutdown used: stop taking new work (retry loop,
        scheduler), let the deliberate fire-and-forget passes finish, then
        release the connections.
        """
        if self._retry_task is not None and not self._retry_task.done():
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._retry_task
        # Post-reply passes are off the reply path by design, so without this a
        # shutdown could drop a reflection or capture write already in flight.
        await background.drain()
        if self.scheduler is not None:
            self.scheduler.shutdown(wait=False)  # type: ignore[attr-defined]
        if self.telegram is not None:
            await self.telegram.close()
        await self.index.close()
        await self.jev.close()


async def _build_index(
    llm: LLMClient, jev: JevClient, postgres: PostgresMode
) -> tuple[MemoryIndex | NullIndex, str | None]:
    """Connect the pgvector index, or degrade (`auto`) / raise (`require`)."""
    index = MemoryIndex(settings.postgres_dsn, llm, reranker=JevReranker(jev))
    try:
        await index.connect()
    except Exception as exc:
        if postgres == "require":
            raise
        reason = f"no Postgres at {settings.postgres_dsn} ({type(exc).__name__}: {exc})"
        log.warning("degraded session: %s", reason)
        return NullIndex(settings.postgres_dsn, llm), reason
    return index, None


async def _open_checkpointer(
    stack: AsyncExitStack, *, degraded_reason: str | None
) -> AsyncPostgresSaver | MemorySaver:
    """Durable checkpointer when there is a database, in-memory otherwise."""
    if degraded_reason is not None:
        log.warning("degraded session: checkpointer is in-memory (threads will not survive exit)")
        return MemorySaver()
    saver = await stack.enter_async_context(
        AsyncPostgresSaver.from_conn_string(_checkpointer_dsn(settings.postgres_dsn))
    )
    await saver.setup()
    return saver


async def _start_services(
    brain: Harness, files: WorkspaceFiles, graph: ChatGraph, runtime: Runtime
) -> None:
    """Scheduler, scheduled tasks and the Telegram channel — the API's shape.

    The CLI opens the harness with `services=False`: a chat REPL is not a
    service, and it must not start a cron loop or claim the Telegram channel.
    """
    scheduler = build_scheduler(runtime)
    scheduler.start()
    log.info("scheduler started: %s", [j.id for j in scheduler.get_jobs()])
    brain.scheduler = scheduler

    task_scheduler = TaskScheduler(
        TaskStore(files.root / "config" / "tasks.json"),
        runtime,
        graph,
        scheduler,
    )
    task_scheduler.register_all()
    runtime.tasks = task_scheduler

    def _on_onboarded() -> None:
        reschedule_nightly(scheduler, owner_sleep_hour(files.root))

    runtime.on_onboarded = _on_onboarded

    telegram = TelegramMCPClient(settings.telegram_mcp_url)
    brain.telegram = telegram
    if await telegram.connect():
        runtime.telegram = telegram
        return

    # Bridge may still be starting; retry in the background so the channel
    # appears as soon as it is reachable (no boot dependency). The task is held
    # on the harness: an unreferenced task can be garbage-collected mid-flight,
    # which for a retry loop means the channel never reconnects.
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

    brain._retry_task = asyncio.create_task(_retry_telegram())


@asynccontextmanager
async def harness(
    *,
    workspace_dir: Path | None = None,
    postgres: PostgresMode = "auto",
    services: bool = True,
) -> AsyncIterator[Harness]:
    """Boot the engine and yield it.

    `postgres="require"` fails at boot when the database is missing (the API's
    contract). `postgres="auto"` degrades instead (the CLI's contract).
    `services=True` also starts the scheduler, the scheduled-task store and the
    Telegram channel.
    """
    # A malformed tool-policy override is a security knob that failed to parse,
    # so it fails the boot rather than being skipped silently. An override that
    # names nothing (a typo) is *not* fatal — `iris tools` reports it.
    parse_overrides(settings.tool_policy_overrides)

    root = Path(workspace_dir if workspace_dir is not None else settings.workspace_dir)
    files = WorkspaceFiles(root)
    sync_owner_chat_id()

    ledger = CostLedger(files.root / "config" / "llm_calls.jsonl")
    llm = LLMClient(ledger=ledger)

    # JEV (TypeSafe System One): typed judgments for recall reranking, skill
    # selection and untrusted-content screening. Optional by design — when the
    # key is absent every integration falls back to the deterministic path.
    jev = JevClient(ledger=ledger)
    if jev.enabled:
        log.info("jev enabled (model=%s)", settings.jev_model)
    else:
        log.info("jev disabled (%s): using deterministic paths", jev.unavailable_reason())

    index, degraded_reason = await _build_index(llm, jev, postgres)
    mode: RunMode = "degraded" if degraded_reason else "full"

    reindexer = Reindexer(files, index, llm)
    if degraded_reason is None:
        try:
            n = await reindexer.reindex_all()
            log.info("memory index ready: %d chunks reindexed", n)
        except Exception as exc:  # noqa: BLE001 - boot must not die on a bad index
            log.warning("reindex skipped at boot: %s", exc)

    async with AsyncExitStack() as stack:
        saver = await _open_checkpointer(stack, degraded_reason=degraded_reason)

        runtime = Runtime(
            files=files,
            llm=llm,
            index=index,
            reindexer=reindexer,
            dreams=DreamEngine(llm, files, index),
            forgetting=ForgettingEngine(index),
            # Declared tool names, not a live runtime: manifests are validated
            # against the full possible tool surface regardless of which
            # channels happen to be connected at this moment.
            skills=open_registry(files, known_tools=TOOL_NAMES),
            sandbox=Sandbox(Path(settings.sandbox_dir)),
            jev=jev,
            traces=TraceLogger(files.root / "config" / "traces.jsonl"),
        )

        from iris_ai.agent.subagents import ResearchSubagent

        runtime.research = ResearchSubagent(runtime)

        # P5: the delegation policy. Constructing it calls no model — it is pure
        # policy (role allowlists, call caps, the deadline) and only does work
        # when the lead actually delegates.
        from iris_ai.agents.orchestrator import Orchestrator

        runtime.orchestrator = Orchestrator(runtime)

        # P8: the pre-tool guard chain, with a budget whose day counters persist
        # to config/budget.json so a cross-session ceiling survives a restart.
        from iris_ai.budget import Budget, BudgetPolicy
        from iris_ai.guards import GuardChain

        budget = Budget(
            policy=BudgetPolicy.from_settings(),
            path=files.root / "config" / "budget.json",
        )
        runtime.guards = GuardChain.from_settings(budget)

        # P7: computer-use is opt-in. Off means the provider, the allowlists and
        # the audit log do not exist and the `computer` tool is not registered —
        # not "registered but refusing", which leaves a capability one
        # misconfiguration away from acting.
        if settings.computer_enabled:
            from iris_ai.computer import computer_from_settings

            runtime.computer = computer_from_settings(files.root)
            available, reason = runtime.computer.availability()
            log.info(
                "computer-use enabled (provider=%s, available=%s%s)",
                settings.computer_provider,
                available,
                "" if available else f", {reason}",
            )

        graph = ChatGraph(runtime, saver)

        brain = Harness(
            files=files,
            llm=llm,
            ledger=ledger,
            jev=jev,
            index=index,
            runtime=runtime,
            graph=graph,
            mode=mode,
            degraded_reason=degraded_reason,
        )

        if services:
            await _start_services(brain, files, graph, runtime)

        try:
            yield brain
        finally:
            await brain.aclose()
