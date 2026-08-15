"""Live smoke test: real Gemini models, full memory pipeline (Chapter 2 verify)."""

import asyncio
import glob
from pathlib import Path

from iris.config import settings
from iris.memory.files import WorkspaceFiles
from iris.memory.index import MemoryIndex
from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin, Provenance
from iris.memory.write import WritePath


async def main() -> None:
    files = WorkspaceFiles(Path(settings.workspace_dir))
    llm = LLMClient()
    index = MemoryIndex(settings.postgres_dsn, llm)
    await index.connect()
    writer = WritePath(llm, files.staging_dir())

    existing = files.memory.read_text()[:1500] if files.memory.exists() else ""
    print("--- 1. extract from a conversation (cheap model) ---")
    cands = await writer.extract_candidates(
        user_message=(
            "Hi Iris! My name is Omar. My favorite language is Python and I am "
            "building a memory engine. Also, I hate Mondays."
        ),
        assistant_reply="Nice to meet you Omar!",
        existing_memory=existing,
    )
    for c in cands:
        print(f"  [{c.op}] imp={c.importance} {c.content}")

    print("--- 2. stage to .dreams (agent provenance, tainted) ---")
    writer.stage(cands, provenance=Provenance(origin=Origin.AGENT, source="chat", session_id="smoke"))
    for p in glob.glob(str(settings.workspace_dir) + "/.dreams/staging-*.jsonl"):
        print(f"  staging file: {p}")

    print("--- 3. recall: what is my name? ---")
    for h in await index.search("what is the user's name?", top_k=3):
        print(f"  {h.score:.3f} [{h.origin.value}] {h.content[:80]}")

    print("--- 4. recall: what language do I like? ---")
    for h in await index.search("favorite programming language", top_k=3):
        print(f"  {h.score:.3f} [{h.origin.value}] {h.content[:80]}")

    await index.close()


if __name__ == "__main__":
    asyncio.run(main())