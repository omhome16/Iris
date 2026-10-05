"""Runtime — the wiring point for every engine component the agent uses.

One object, built at app boot, passed to tools, graphs and the API. Keeps the
agent code free of global state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from iris_ai.channels.telegram_mcp import TelegramMCPClient
from iris_ai.computer.session import Computer
from iris_ai.guards import GuardChain
from iris_ai.hooks import HookBus
from iris_ai.jev.client import JevClient
from iris_ai.memory.dreaming import DreamEngine
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.forgetting import ForgettingEngine
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.indexer import Reindexer
from iris_ai.memory.llm import LLMClient
from iris_ai.sandbox import Sandbox
from iris_ai.skills.registry import SkillRegistry
from iris_ai.tasks import TaskScheduler
from iris_ai.trace import TraceLogger

if TYPE_CHECKING:
    from iris_ai.kernel.journal import TurnJournal
    from iris_ai.mcp.provider import McpPool

# The session whose turn is being executed, visible to tools (set by the
# graph's tools node). Tools like schedule_task use it to keep tasks bound
# to the conversation they were created in instead of a hardcoded thread.
current_session: ContextVar[str] = ContextVar("iris_current_session", default="")

# Who is driving the turn (`owner`, `agent`, …). Tools that stamp provenance
# read this instead of assuming the owner. Set by the tools node; `owner`
# outside a turn, which is the direct-dispatch case tests use.
current_origin: ContextVar[str] = ContextVar("iris_current_origin", default="owner")

# The id of the tool call being executed (set by the graph's tools node).
# Approval payloads carry it so an approval can be granted exactly once — see
# iris/approval.py. Empty outside a tool call, which is exactly when no approval
# can be in flight.
current_tool_call: ContextVar[str] = ContextVar("iris_current_tool_call", default="")

# Tools loaded onto the visible surface during this turn's tool node (set by the
# tools node, appended to by `find_tools`). A list rather than the state itself,
# because the node is what owns the state write: a tool cannot return a state
# update, so it records here and the node folds this into `loaded_tools`.
# `None` means "no tool node is running" — a direct `dispatch` from a test — and
# loading is then a no-op rather than an error.
current_loaded_tools: ContextVar[list[str] | None] = ContextVar(
    "iris_current_loaded_tools", default=None
)

# The session context the surface is being built under: `(origin, active skills)`,
# set by the tools node. `find_tools` must offer exactly what `tool_surface` will
# later append to the prompt, or it would report a tool as loaded that the
# session's rule then refuses — telling the model it holds something it does not
# have, which is the guessing this mechanism exists to prevent. `None` means
# "no tool node is running", and the caller assumes the unrestricted owner case.
current_tool_scope: ContextVar[tuple[str, tuple[str, ...]] | None] = ContextVar(
    "iris_current_tool_scope", default=None
)


@dataclass(slots=True)
class Runtime:
    files: WorkspaceFiles
    llm: LLMClient
    index: MemoryIndex
    reindexer: Reindexer
    dreams: DreamEngine
    forgetting: ForgettingEngine
    # The skill registry (P4): reads span every source; writes delegate to the
    # SkillLibrary, which stays the only writer (dreaming, skill_write, the
    # reinforce/revise loop).
    skills: SkillRegistry
    sandbox: Sandbox
    # Typed-judgment layer (TypeSafe JEV). Optional: every consumer degrades
    # to its deterministic path when this is None or disabled.
    jev: JevClient | None = None
    telegram: TelegramMCPClient | None = None
    # Every connected channel by name (the Telegram entry is also exposed as
    # `telegram` for the tools that predate the channel registry).
    channels: dict[str, object] = field(default_factory=dict)
    tasks: TaskScheduler | None = None
    # The pre-P5 researcher, kept working (it is now the `researcher` role with
    # a compatibility wrapper). The orchestrator below is the P5 path: it owns
    # the budgets, the refusals and the merge.
    research: object | None = None
    # Delegate policy (P5). Optional so a library caller can build a Runtime
    # without the multi-agent layer; tools fall back to `research` without it.
    orchestrator: object | None = None
    # Computer-use (P7). Built only when `computer_enabled`; None means the
    # `computer` tool is not registered at all, so a capability nobody granted
    # is not one tool-call away.
    computer: Computer | None = None
    # Pre-tool guard chain (P8). Optional so a library caller can build a
    # Runtime without it; the graph falls back to settings.
    guards: GuardChain | None = None
    # Lifecycle hook bus. The guard chain is its first subscriber; a plugin can
    # attach policy/audit/instrumentation to the same ordered events. Optional so
    # a library caller can build a Runtime without it; the graph creates one.
    hooks: HookBus | None = None
    traces: TraceLogger | None = None
    # The turn journal (Phase 5): what a turn did, durably enough that a restart
    # can replay a settled tool call instead of re-running it. `None` means the
    # boundary is a no-op — the graph still runs, it just cannot be exactly-once.
    journal: TurnJournal | None = None
    # The MCP capability pool (Phase 3): declared external servers, connected
    # once for the process's lifetime. `None` means no server was declared — not
    # "declared but down", which the pool reports per server instead, because
    # "you have no memory server configured" and "your memory server is not
    # running" are different answers and only one of them is the owner's fault.
    mcp: McpPool | None = None
    # Swappable pieces. None means the built-in context assembler and capture.
    context_builder: object | None = None
    capture_policy: object | None = None
    # A consolidator stage that proposes conflicts. Dreaming still writes.
    consolidator_stage: object | None = None
    # Which persona the prompt should load: blank, file, a preset, or a catalog name.
    persona_choice: str = "file"
    engine_name: str = "react"
    engine_fails: int = 0
    pipelines: dict = field(default_factory=dict)
    pipeline_fails: dict = field(default_factory=dict)
    # What was attached for this process: kind -> {name, source, digest}.
    # /explain and the turn trace read it. Empty until `components.attach`.
    harness_identity: dict = field(default_factory=dict)
    # Identity of the component set last swapped in. A turn pins this at start.
    generation_id: str = ""
    on_onboarded: Callable[[], None] | None = field(default=None, init=False)
