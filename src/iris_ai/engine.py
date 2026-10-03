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
      → reindex → thread store → Runtime → ChatGraph
      → (services) scheduler + task scheduler + Telegram channel

Every client opens a `Harness` the same way. The CLI is the reason for the
degraded mode: when no Postgres is reachable, `postgres="auto"` keeps the
conversation alive with an in-memory thread store and no vector recall, and
says so — `mode`, `degraded_reason`, and a recall error that names the fix.
The HTTP API uses `postgres="auto"` on a SQLite install and `postgres="require"`
when memory is pgvector or the checkpointer is pinned to Postgres, so a missing
database still fails that boot.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import socket
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from iris_ai import background
from iris_ai.agent.chat import ChatGraph
from iris_ai.agent.runtime import Runtime
from iris_ai.agent.tools import TOOL_NAMES
from iris_ai.capabilities.judges import JUDGES, discover_judges
from iris_ai.capabilities.memory import MEMORY_BACKENDS, discover_memory_backends
from iris_ai.capabilities.models import MODELS, discover_models
from iris_ai.channels.base import Channel
from iris_ai.channels.registry import build_channel, channel_specs, connect_channels, discover_channels
from iris_ai.channels.telegram_mcp import TelegramMCPClient
from iris_ai.config import settings
from iris_ai.jev.client import JevClient
from iris_ai.jev.recall import JevReranker
from iris_ai.kernel import TurnKernel, build_journal
from iris_ai.kernel.threads import MemoryThreadStore, PostgresThreadStore, SqliteThreadStore
from iris_ai.ledger import CostLedger
from iris_ai.manifest import apply_manifest, load_manifest
from iris_ai.mcp.provider import McpPool
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
from iris_ai.toolregistry import discover_tool_providers
from iris_ai.trace import TraceLogger

log = logging.getLogger("iris_ai.harness")

PostgresMode = Literal["require", "auto"]
RunMode = Literal["full", "degraded"]


def _checkpointer_dsn(dsn: str) -> str:
    """The Postgres URL for a shared thread store, with a connect deadline.

    Two conversions, both because this is the *psycopg* endpoint:

    - `postgresql+psycopg://` is SQLAlchemy's spelling; psycopg wants the plain
      scheme.
    - `connect_timeout` is required, not decorative. psycopg's async connect
      falls back to a 130-second deadline, and on Windows an unreachable server
      never resumes it early: a refused connect is reported in the *exceptional*
      fd set, which an asyncio selector loop does not watch, so the writer
      callback never fires and the poll runs to the deadline instead of failing
      at once. A 130-second stall is indistinguishable from a hang, and it is
      exactly the case the thread-store ladder exists to degrade around — so the
      deadline comes from settings. (asyncpg, which backs the pgvector index,
      reports the refusal by itself; this is not a general no-timeouts rule.)

    A `connect_timeout` already in the DSN wins: the DSN is the operator's last
    word, and this only fills in what a default DSN cannot know.
    """
    plain = dsn.replace("postgresql+psycopg://", "postgresql://")
    if "connect_timeout" in plain:
        return plain
    seconds = max(2, int(settings.postgres_connect_timeout))
    return f"{plain}{'&' if '?' in plain else '?'}connect_timeout={seconds}"


def _postgres_driver_installed() -> bool:
    """True when the optional Postgres driver can be imported."""
    try:
        import asyncpg  # noqa: F401
    except ImportError:
        return False
    return True


def _postgres_endpoint(dsn: str) -> tuple[str, int] | None:
    """The DSN's TCP endpoint, or None when it has none to probe."""
    parts = urlsplit(dsn.replace("postgresql+psycopg://", "postgresql://"))
    if not parts.hostname:
        # A unix socket, or libpq's own default host: only the driver knows.
        return None
    return parts.hostname, parts.port or 5432


async def _postgres_accepts_connections(dsn: str) -> bool:
    """Is anything accepting TCP connections at the configured endpoint?

    The `auto` ladder asks this before it opens Postgres, because a refused
    connect can stall for the whole connect deadline on Windows (see
    `_checkpointer_dsn`). One blocking `connect()` answers the same question in
    microseconds — the kernel already knows, and a refusal is delivered to the
    caller rather than to the event loop. Without this, every boot on a machine
    with no Postgres pays the deadline before falling through to SQLite.

    A DSN with no TCP endpoint answers True: there is nothing here to probe, and
    the driver's result is then the only honest one.

    The blocking call is deliberate (that is what makes it instant) and is kept
    off the event loop.
    """
    endpoint = _postgres_endpoint(dsn)
    if endpoint is None:
        return True
    host, port = endpoint

    def _connect() -> bool:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:  # refused, unresolvable, or nothing answered in time
            return False

    return await asyncio.to_thread(_connect)


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
    #: `postgres` | `sqlite` | `memory` — which store is holding the threads.
    checkpointer: str = ""
    telegram: TelegramMCPClient | None = None
    channels: dict[str, Channel] = field(default_factory=dict)
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
    ):
        """Streamed turn. Yields typed events that also unpack as `(mode, payload)`."""
        return self.graph.respond_stream(message, session_id=session_id, image=image)

    # ── shutdown ─────────────────────────────────────────────────────────
    async def reload(self) -> str:
        """Re-read config and swap components without leaving the process.

        Threads stay in the checkpointer. A component that fails to load is
        left on the previous one; `attach` reports that instead of crashing.
        """
        from iris_ai.components import attach
        from iris_ai.config import reload as reload_settings
        from iris_ai.manifest import load_manifest

        reload_settings()
        attach(self.runtime, load_manifest(Path(settings.harness_config)))
        return "reloaded config and components"

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
        for _name, channel in self.channels.items():
            if channel is self.telegram:
                continue
            with contextlib.suppress(Exception):
                await channel.close()
        if self.telegram is not None:
            await self.telegram.close()
        # Idempotent, and owned by the boot stack as well: closing here keeps the
        # shutdown order explicit (stop taking work, release what this process
        # opened) rather than leaving the pool to a context manager finaliser.
        if self.runtime.mcp is not None:
            await self.runtime.mcp.close()
        await self.index.close()
        await self.jev.close()


def _memory_index(llm: LLMClient, _jev: JevClient, reranker: JevReranker):
    """The configured store: a local component, a dotted class, or the registry."""
    from iris_ai.plug import construct, load_class, local_folder

    name = settings.memory_backend
    folder = local_folder("memory", name)
    if folder is not None:
        return construct(load_class(folder), None)
    if ":" in name:
        from iris_ai.components import load_symbol

        return construct(load_symbol(name), None)
    return MEMORY_BACKENDS.build(name, dsn=settings.postgres_dsn, llm=llm, reranker=reranker)


async def _build_index(
    llm: LLMClient, jev: JevClient, postgres: PostgresMode
) -> tuple[MemoryIndex | NullIndex, str | None]:
    """Build and connect the configured memory backend, or degrade / raise.

    The backend is chosen through the registry (`settings.memory_backend`), so a
    new store is a registration + a setting, not a branch here. Degradation is
    still the caller's decision: `postgres="require"` fails the boot, `auto`
    falls back to the ``null`` backend and names the reason.
    """
    reranker = JevReranker(jev)
    index = _memory_index(llm, jev, reranker)
    try:
        await index.connect()
    except Exception as exc:
        if postgres == "require":
            raise
        reason = f"no Postgres at {settings.postgres_dsn} ({type(exc).__name__}: {exc})"
        log.warning("degraded session: %s", reason)
        return (
            MEMORY_BACKENDS.build("null", dsn=settings.postgres_dsn, llm=llm, reranker=reranker),
            reason,
        )
    return index, None


async def _open_checkpointer(
    stack: AsyncExitStack, *, postgres: PostgresMode
) -> tuple[PostgresThreadStore | SqliteThreadStore | MemoryThreadStore, str]:
    """Keep conversation threads durable without requiring a service.

    Three tiers, tried in order and reported rather than guessed:

    1. **Postgres** — what a multi-process deployment wants, and what
       ``CHECKPOINTER_BACKEND=postgres`` (or ``postgres="require"``) pins.
    2. **SQLite** — one file, no daemon. Threads survive a restart, which is the
       whole reason the in-memory fallback is a poor default for a chat app.
    3. **In-memory** — the last resort, with a warning that threads will not
       survive exit. Reached only when the configured tier is unavailable.

    This is deliberately *separate* from the memory backend: which store holds
    recall and which store holds threads are independent choices, and tying
    them meant a SQLite memory user silently lost durable threads.

    The Postgres tier is tried through `_checkpointer_dsn`, so an unreachable
    server fails after the configured connect deadline instead of stalling the
    boot — that is what makes "postgres unreachable" a tier outcome rather than
    an outage.
    """
    want = settings.checkpointer_backend

    # An *optional* Postgres tier is not worth a connect deadline. `auto` on both
    # axes means the tier may be skipped, so ask the cheap question first: with
    # nothing listening there is nothing for psycopg to report. A pinned tier
    # (`CHECKPOINTER_BACKEND=postgres`) or the API's contract (`postgres=require`)
    # still goes straight to the driver, where the driver's own error is the
    # answer the caller is owed.
    # A fresh install has no Postgres extra and no server. Trying the default
    # DSN only to report "asyncpg is not installed" is noise, not a failure.
    postgres_ready = _postgres_driver_installed()
    if want == "auto" and postgres == "auto" and not postgres_ready:
        log.debug("checkpointer: asyncpg is not installed; skipping Postgres")
    elif (
        want == "auto"
        and postgres == "auto"
        and not await _postgres_accepts_connections(settings.postgres_dsn)
    ):
        log.debug(
            "checkpointer: nothing listening at %s — skipping the Postgres tier",
            settings.postgres_dsn,
        )
    elif want in ("auto", "postgres"):
        try:
            saver = PostgresThreadStore(settings.postgres_dsn)
            await saver.connect()
            stack.push_async_callback(saver.close)
            return saver, "postgres"
        except Exception as exc:  # the fallback chain is the point; `raise` below
            if want == "postgres" or postgres == "require":
                # An explicit request, or the API's contract: a missing database
                # is a boot failure, exactly as it always was.
                raise
            log.debug(
                "checkpointer: Postgres unreachable (%s: %s); falling back",
                type(exc).__name__,
                exc,
            )

    if want in ("auto", "sqlite", "postgres"):
        try:
            path = Path(settings.checkpointer_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            saver = SqliteThreadStore(str(path))
            stack.push_async_callback(saver.close)
            return saver, "sqlite"
        except Exception as exc:  # see above; `raise` below is the pinned tier
            if want == "sqlite":
                raise
            log.warning(
                "checkpointer: SQLite unavailable (%s: %s); using in-memory",
                type(exc).__name__,
                exc,
            )

    log.warning("degraded session: checkpointer is in-memory (threads will not survive exit)")
    return MemoryThreadStore(), "memory"


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

    # Channels are config-driven: installed plugins are discovered, the enabled
    # set is resolved from settings, and each is connected independently. A
    # down channel is skipped, never fatal — the old Telegram-only retry is now
    # a general "retry whatever did not connect" loop.
    discover_channels()
    specs = channel_specs()
    connected = await connect_channels(specs)
    brain.channels = dict(connected)
    runtime.channels = dict(connected)
    if "telegram" in connected:
        brain.telegram = connected["telegram"]  # type: ignore[assignment]
        runtime.telegram = connected["telegram"]  # type: ignore[assignment]

    missing = [spec for spec in specs if spec.name not in connected]
    if missing:
        names = [spec.name for spec in missing]
        log.warning("channel(s) not connected; retrying in background: %s", names)

        # Held on the harness: an unreferenced task can be garbage-collected
        # mid-flight, which for a retry loop means the channel never reconnects.
        async def _retry_channels() -> None:
            pending = {spec.name: spec for spec in missing}
            delay = 5.0
            while pending:
                await asyncio.sleep(delay)
                for name, spec in list(pending.items()):
                    channel = build_channel(spec)
                    if not await channel.connect():
                        continue
                    connected[name] = channel
                    brain.channels = dict(connected)
                    runtime.channels = dict(connected)
                    if name == "telegram":
                        brain.telegram = channel  # type: ignore[assignment]
                        runtime.telegram = channel  # type: ignore[assignment]
                    del pending[name]
                    log.info("channel connected on retry: %s", name)
                delay = min(delay * 1.5, 120.0)

        brain._retry_task = asyncio.create_task(_retry_channels())


def _apply_harness_overrides(
    *,
    provider: str | None,
    model: str | None,
    api_key: str | None,
    memory: str | None,
) -> None:
    """Library callers pass these instead of editing `.env`. They win over files."""
    if not any((provider, model, api_key, memory)):
        return
    from iris_ai.config import reload as reload_settings
    from iris_ai.providers import PROVIDERS, qualify

    overrides: dict[str, object] = {}
    chosen = (provider or "").strip().lower()
    if chosen:
        overrides["llm_provider"] = chosen
    if memory:
        overrides["memory_backend"] = memory
    if model:
        qualified = qualify(chosen, model) if chosen else model.strip()
        overrides["strong_model"] = qualified
        spec = PROVIDERS.get(chosen)
        if spec is not None:
            overrides[spec.strong_field] = qualified
    if api_key and chosen:
        spec = PROVIDERS.get(chosen)
        if spec is not None and spec.key_field:
            overrides[spec.key_field] = api_key
    if overrides:
        reload_settings(**overrides)


@asynccontextmanager
async def harness(
    *,
    workspace_dir: Path | None = None,
    postgres: PostgresMode = "auto",
    services: bool = True,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    memory: str | None = None,
) -> AsyncIterator[Harness]:
    """Boot the engine and yield it.

    `postgres="require"` fails at boot when the database is missing.
    `postgres="auto"` degrades instead (the CLI's contract, and the HTTP API
    on a SQLite install).
    `services=True` also starts the scheduler, the scheduled-task store and the
    Telegram channel.

    `provider`, `model`, `api_key` and `memory` override the files for this
    process. One process has one configuration.
    """
    # Declarative config, applied before anything reads a setting. Precedence is
    # defaults < .env < manifest < environment, so a real env var always wins, a
    # profile can own a key that the sample `.env` also mentions (see
    # `iris_ai.PRELOADED_ENV`), and the file stays committable.
    manifest_path = Path(settings.harness_config)
    applied = apply_manifest(settings, load_manifest(manifest_path))
    if applied:
        log.info("harness manifest applied (%s): %s", manifest_path, applied)
    _apply_harness_overrides(provider=provider, model=model, api_key=api_key, memory=memory)

    # A malformed tool-policy override is a security knob that failed to parse,
    # so it fails the boot rather than being skipped silently. An override that
    # names nothing (a typo) is *not* fatal — `iris tools` reports it.
    parse_overrides(settings.tool_policy_overrides)
    # Discover installed tool plugins once. Core's tools are already registered;
    # this only adds packages advertising the `iris_ai.tools` entry-point group.
    discover_tool_providers()

    root = Path(workspace_dir if workspace_dir is not None else settings.workspace_dir)
    files = WorkspaceFiles(root)
    from iris_ai.logging_setup import configure_logging

    configure_logging(files.root)
    sync_owner_chat_id()

    # Discover installed capability plugins once, then build the configured
    # implementation of each kind through its registry. Core ships litellm /
    # pgvector / jev; a plugin registers another and `settings.*_backend` selects
    # it — the boot path never names a concrete class.
    discover_models()
    discover_memory_backends()
    discover_judges()

    ledger = CostLedger(files.root / "config" / "llm_calls.jsonl")
    llm = MODELS.build(settings.model_backend, ledger=ledger)

    # JEV (TypeSafe System One): typed judgments for recall reranking, skill
    # selection and untrusted-content screening. Optional by design — when the
    # key is absent every integration falls back to the deterministic path.
    jev = JUDGES.build(settings.judge_backend, ledger=ledger)
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
        saver, checkpointer = await _open_checkpointer(stack, postgres=postgres)
        log.info("checkpointer: %s", checkpointer)

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
            # Phase 5: the turn journal. A previous process that was killed
            # mid-tool leaves a step that never settled, and the boot says so —
            # the alternative is discovering it from a side effect later.
            journal=build_journal(files.root),
        )

        for _thread, _turn in TurnKernel(runtime.journal).pending():
            log.warning(
                "turn %s on thread %s stopped mid-tool: a side-effecting action may or "
                "may not have completed, and will not be repeated automatically",
                _turn,
                _thread,
            )

        from iris_ai.agent.subagents import ResearchSubagent

        runtime.research = ResearchSubagent(runtime)

        # P5: the delegation policy. Constructing it calls no model — it is pure
        # policy (role allowlists, call caps, the deadline) and only does work
        # when the lead actually delegates.
        from iris_ai.agents.orchestrator import Orchestrator
        from iris_ai.agents.roles import roles_from_manifest

        runtime.orchestrator = Orchestrator(
            runtime, roles=roles_from_manifest(load_manifest(manifest_path))
        )

        # P8: the pre-tool guard chain, with a budget whose day counters persist
        # to config/budget.json so a cross-session ceiling survives a restart.
        from iris_ai.budget import Budget, BudgetPolicy
        from iris_ai.guards import GuardChain

        budget = Budget(
            policy=BudgetPolicy.from_settings(),
            path=files.root / "config" / "budget.json",
        )
        runtime.guards = GuardChain.from_settings(budget)

        # The lifecycle bus is built here (rather than inside the graph) so
        # telemetry can subscribe before the guard chain does. Ordering is the
        # point: the guards run at -100, telemetry at -50, so built-in policy
        # decides before anything observes the decision.
        from iris_ai.hooks import HookBus, discover_hooks

        runtime.hooks = HookBus()
        if settings.otel_exporter != "none":
            from iris_ai.observability.otel import exporter, install

            provider = exporter(endpoint=settings.otel_endpoint, service_name=settings.agent_name)
            install(runtime.hooks, agent=settings.agent_name, tracer=provider.get_tracer("iris"))
            log.info("telemetry enabled: otlp (service=%s)", settings.agent_name)
        # Installed hook plugins attach last: they default to priority 0, so
        # built-in policy (-100) and telemetry (-50) have already decided and
        # observed by the time an add-on runs. Reported rather than silent, for
        # the same reason every other discovery is.
        attached = discover_hooks(runtime.hooks)
        if attached:
            log.info("hook plugins attached: %s", ", ".join(attached))

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

        # Phase 3: the MCP capability pool. Declared servers connect **once**, on
        # this exit stack, so the connections are released on every shutdown path
        # (including a boot that fails after this line). A server that is down is
        # a logged, reported, per-server failure — never a reason not to boot.
        # The judgment layer goes in too: a `review`/`untrusted` server's output is
        # screened for instruction injection before the model reads it. Without a
        # judge the output is still tagged (`screened: false`), never passed off as
        # checked.
        runtime.mcp = await stack.enter_async_context(McpPool(jev=jev))
        # A server that is merely not up yet joins on its own; the task is held by
        # the pool, which cancels it on close.
        runtime.mcp.start_retry()

        from iris_ai.components import attach

        attach(runtime, load_manifest(manifest_path))
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
            checkpointer=checkpointer,
        )

        if services:
            await _start_services(brain, files, graph, runtime)

        try:
            yield brain
        finally:
            await brain.aclose()
