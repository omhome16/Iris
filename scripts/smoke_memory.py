"""Live smoke test: real models, memory pipeline v2 (note → daily note → dreaming → recall)."""

import asyncio
from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.dreaming import LightPhase
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.index import MemoryIndex
from iris_ai.memory.llm import LLMClient


async def main() -> None:
    files = WorkspaceFiles(Path(settings.workspace_dir))
    llm = LLMClient()
    index = MemoryIndex(settings.postgres_dsn, llm)
    await index.connect()

    print("--- 1. agent note → today's daily note (ADD-only, marked) ---")
    entry = "- [7] The owner's favorite language is Python (triggers: python, programming) (note)"
    files.append_daily(entry, stamp=False)
    print(f"  appended: {entry}")

    print("--- 2. light phase picks up (note) lines from daily notes ---")
    promoted, _staged = LightPhase().run(
        files.staging_dir(),
        daily_dir=files.root / "memory",
        scan_days=settings.dream_note_scan_days,
    )
    for s in promoted:
        print(f"  [{s.op}] imp={s.importance:.0f} {s.content} (from {s.provenance.source})")

    print("--- 3. recall: what language do I like? ---")
    for h in await index.search("favorite programming language", top_k=3):
        print(f"  {h.score:.3f} [{h.origin.value}] {h.content[:80]}")

    print("--- 4. escalate lane: recent daily notes ---")
    for h in await index.escalate("python language", top_k=3):
        print(f"  {h.score:.3f} [{h.origin.value}] {h.content[:80]}")

    await index.close()


if __name__ == "__main__":
    asyncio.run(main())
