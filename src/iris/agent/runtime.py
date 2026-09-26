"""Runtime — the wiring point for every engine component the agent uses.

One object, built at app boot, passed to tools, graphs and the API. Keeps the
agent code free of global state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

from iris.channels.telegram_mcp import TelegramMCPClient
from iris.computer.session import Computer
from iris.guards import GuardChain
from iris.jev.client import JevClient
from iris.memory.dreaming import DreamEngine
from iris.memory.files import WorkspaceFiles
from iris.memory.forgetting import ForgettingEngine
from iris.memory.index import MemoryIndex
from iris.memory.indexer import Reindexer
from iris.memory.llm import LLMClient
from iris.sandbox import Sandbox
from iris.skills.registry import SkillRegistry
from iris.tasks import TaskScheduler
from iris.trace import TraceLogger

# The session whose turn is being executed, visible to tools (set by the
# graph's tools node). Tools like schedule_task use it to keep tasks bound
# to the conversation they were created in instead of a hardcoded thread.
current_session: ContextVar[str] = ContextVar("iris_current_session", default="")

# The id of the tool call being executed (set by the graph's tools node).
# Approval payloads carry it so an approval can be granted exactly once — see
# iris/approval.py. Empty outside a tool call, which is exactly when no approval
# can be in flight.
current_tool_call: ContextVar[str] = ContextVar("iris_current_tool_call", default="")


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
    traces: TraceLogger | None = None
    on_onboarded: Callable[[], None] | None = field(default=None, init=False)
