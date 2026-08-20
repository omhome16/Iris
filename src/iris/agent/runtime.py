"""Runtime — the wiring point for every engine component the agent uses.

One object, built at app boot, passed to tools, graphs and the API. Keeps the
agent code free of global state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from iris.channels.telegram_mcp import TelegramMCPClient
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
    telegram: TelegramMCPClient | None = None
    tasks: TaskScheduler | None = None
    research: object | None = None
    traces: TraceLogger | None = None
    on_onboarded: Callable[[], None] | None = field(default=None, init=False)