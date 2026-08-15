"""Fresh start — reset Iris to a newborn state.

Wipes ALL memory artifacts (MEMORY.md, USER.md, DREAMS.md, daily notes,
skills, staging, dreams preimages, the index) and resets onboarding, so the
next chat goes through the identity wizard.

WHAT SURVIVES: AGENTS.md (the operating contract), config defaults, .env,
the database schema itself.
WHAT DIES: every memory, every dream, every skill, every staged signal.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from iris.config import settings
from iris.memory.files import WorkspaceFiles
from iris.memory.index import MemoryIndex
from iris.memory.llm import LLMClient
from iris.onboarding import OnboardingState

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

    files.config_file().parent.mkdir(parents=True, exist_ok=True)
    files.config_file().write_text(OnboardingState().to_json(), encoding="utf-8")


async def truncate_index() -> None:
    index = MemoryIndex(settings.postgres_dsn, LLMClient())
    await index.connect()
    async with index._pool.acquire() as conn:  # noqa: SLF001 - setup script
        await conn.execute("TRUNCATE memory_chunks")
    await index.close()


if __name__ == "__main__":
    fresh_start(Path(settings.workspace_dir))
    asyncio.run(truncate_index())
    print("Iris reset to newborn state. AGENTS.md untouched. Say hi to meet her.")