"""Fresh start — reset Iris to a newborn state.

Wipes ALL memory artifacts (MEMORY.md, USER.md, DREAMS.md, daily notes,
skills, staging, dreams preimages, ingested imports, sandbox files,
traces, hallucination flags, scheduled tasks, the index) and resets
onboarding, so the next chat goes through the identity wizard.

WHAT SURVIVES: AGENTS.md (the operating contract), the cost ledger
(config/llm_calls.jsonl — operator accounting, not her memory), config
defaults, .env, the database schema itself.
WHAT DIES: every memory, every dream, every skill, every staged signal,
every ingested document, every pending task.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.llm import LLMClient
from iris_ai.onboarding import OnboardingState

HEADER_MEMORY = """# MEMORY.md — Iris long-term memory

> Curated by dreaming consolidation. Small, compact, durable facts only.
> Detail lives in `memory/YYYY-MM-DD.md`. Superseded entries are retired
> with keys, never deleted silently.

_Empty — Iris is not yet born. Onboarding will fill USER.md; daily life will
fill this file over days and dreams._
"""

HEADER_USER = """# USER.md — the owner's profile

> Stable preferences, communication style, relationships, active projects.
> Loaded at session start within a separate budget. Written during onboarding
> and refined by dreaming.

_Empty — onboarding will define the owner's profile here._
"""

HEADER_DREAMS = """# DREAMS.md — Iris's dream diary

> Human-readable record of every sleep cycle. Read-only for Iris.
"""


def fresh_start(workspace: Path) -> None:
    files = WorkspaceFiles(workspace)
    files.write_curated(files.memory, HEADER_MEMORY)
    files.write_curated(files.user, HEADER_USER)
    files.write_curated(files.dreams, HEADER_DREAMS)

    for p in files.daily_note().parent.glob("*.md"):
        p.unlink()
    for p in files.skills_dir().glob("*.md"):
        p.unlink()
    for p in files.skills_dir().glob("*.json"):
        p.unlink()
    shutil.rmtree(files.root / ".dreams", ignore_errors=True)
    (files.root / ".dreams").mkdir(parents=True, exist_ok=True)
    # ingested web documents are UNTRUSTED memory — a newborn has none
    shutil.rmtree(files.root / "imports", ignore_errors=True)
    # sandbox files are hers — a newborn has a clean box
    shutil.rmtree(files.root / "sandbox", ignore_errors=True)
    (files.root / "sandbox").mkdir(parents=True, exist_ok=True)
    # scheduled tasks and turn telemetry don't survive a reset either
    for stale in (
        files.root / "config" / "tasks.json",
        files.root / "config" / "traces.jsonl",
        files.root / "config" / "traces.jsonl.1",
        files.root / "config" / "hallucination_flags.jsonl",
    ):
        stale.unlink(missing_ok=True)

    files.config_file().parent.mkdir(parents=True, exist_ok=True)
    files.config_file().write_text(OnboardingState().to_json(), encoding="utf-8")


async def truncate_index() -> None:
    index = MemoryIndex(settings.postgres_dsn, LLMClient())
    await index.connect()
    async with index._pool.acquire() as conn:
        await conn.execute("TRUNCATE memory_chunks")
    await index.close()


if __name__ == "__main__":
    fresh_start(Path(settings.workspace_dir))
    asyncio.run(truncate_index())
    print("Iris reset to newborn state. AGENTS.md untouched. Say hi to meet her.")
