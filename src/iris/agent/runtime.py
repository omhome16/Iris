"""Runtime — the wiring point for every engine component the agent uses.

One object, built at app boot, passed to tools, graphs and the API. Keeps the
agent code free of global state.
"""

from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass, field

from iris.channels.telegram_mcp import TelegramMCPClient
from iris.jev.client import JevClient
from iris.memory.dreaming import DreamEngine
from iris.memory.files import WorkspaceFiles
from iris.memory.forgetting import ForgettingEngine
from iris.memory.index import MemoryIndex
from iris.memory.indexer import Reindexer
from iris.memory.llm import LLMClient
from iris.memory.skills import SkillLibrary
from iris.sandbox import Sandbox
from iris.tasks import TaskScheduler
from iris.trace import TraceLogger

# The session whose turn is being executed, visible to tools (set by the
# graph's tools node). Tools like schedule_task use it to keep tasks bound
# to the conversation they were created in instead of a hardcoded thread.
current_session: ContextVar[str] = ContextVar("iris_current_session", default="")


@dataclass(slots=True)
class Runtime:
    files: WorkspaceFiles
    llm: LLMClient
    index: MemoryIndex
    reindexer: Reindexer
    dreams: DreamEngine
    forgetting: ForgettingEngine
    skills: SkillLibrary
    sandbox: Sandbox
    # Typed-judgment layer (TypeSafe JEV). Optional: every consumer degrades
    # to its deterministic path when this is None or disabled.
    jev: JevClient | None = None
    telegram: TelegramMCPClient | None = None
    tasks: TaskScheduler | None = None
    research: object | None = None
    traces: TraceLogger | None = None
    on_onboarded: Callable[[], None] | None = field(default=None, init=False)
