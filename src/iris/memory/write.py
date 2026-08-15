"""Write path — how Iris turns conversations into durable memory.

Pattern (Mem0 lineage, engineered ours):
1. After a turn, the cheap model extracts candidate memories from the
   message pair (user + Iris), deciding ADD / UPDATE / DELETE / NOOP against
   a compact summary of *already-relevant* memories.
2. Candidates are staged (never written directly) with agent provenance
   into `.dreams/staging/` — the dreaming pass decides what graduates.
3. Explicit "remember this" statements bypass staging: they are written to
   MEMORY.md immediately with owner provenance (the human is the writer).

The staging step is the security gate: content that enters the prompt later
(trigger injection / bootstrap) can only come from promoted, trusted entries.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin, Provenance

_EXTRACTION_SYSTEM = """You are the memory extraction pipeline for Iris, a personal assistant.

Given a conversation turn and the owner's existing relevant memories, decide
which NEW durable facts are worth remembering. Rules:

- Remember durable facts about the owner's life, preferences, decisions,
  relationships, projects, plans, and things they asked you to remember.
- Do NOT remember small talk, greetings, or transient state.
- Facts that contradict an existing memory should be op UPDATE (with the
  target key from the existing memory) — the old fact is superseded, not deleted.
- op DELETE only when the owner explicitly revokes something.
- NOOP means nothing worth storing.
- Write each memory as a compact, self-contained statement, present tense,
  with dates where relevant ("The owner's lease ends March 2027").
- Assign importance 1-10 (10 = life-shaping, 5 = useful context, 2 = trivia).
- Give 2-5 short trigger phrases (exact-ish words that would make this
  memory relevant in future messages).

Respond ONLY with JSON: {"memories": [{"op": "ADD|UPDATE|DELETE|NOOP",
"content": "...", "importance": 5, "triggers": ["..."], "target": "<supersession key or ''>"}]}"""


@dataclass(slots=True)
class MemoryCandidate:
    op: str = "ADD"
    content: str = ""
    importance: float = 5.0
    triggers: list[str] = field(default_factory=list)
    target: str = ""
    category: str = "life"


class WritePath:
    def __init__(self, llm: LLMClient, staging_dir: Path) -> None:
        self.llm = llm
        self.staging_dir = staging_dir
        self.staging_dir.mkdir(parents=True, exist_ok=True)

    async def extract_candidates(
        self,
        user_message: str,
        assistant_reply: str,
        existing_memory: str,
    ) -> list[MemoryCandidate]:
        """Cheap-model extraction of durable facts from one turn."""
        payload = await self.llm.complete(
            [
                {"role": "system", "content": _EXTRACTION_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"## Existing relevant memories\n{existing_memory or '(none)'}\n\n"
                        f"## User message\n{user_message}\n\n"
                        f"## Iris's reply\n{assistant_reply}\n"
                    ),
                },
            ],
            tier="cheap",
            json_mode=True,
            max_tokens=800,
        )
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return []
        candidates: list[MemoryCandidate] = []
        for item in data.get("memories", []):
            if item.get("op", "NOOP").upper() == "NOOP":
                continue
            candidates.append(
                MemoryCandidate(
                    op=item.get("op", "ADD").upper(),
                    content=str(item.get("content", "")).strip(),
                    importance=float(item.get("importance", 5.0)),
                    triggers=list(item.get("triggers", []))[:5],
                    target=str(item.get("target", "")),
                )
            )
        return [c for c in candidates if c.content]

    def stage(self, candidates: list[MemoryCandidate], *, provenance: Provenance) -> None:
        """Stage candidates for the dreaming pass. Never injected into the
        prompt from here — staging is machine-facing only."""
        if not candidates:
            return
        day = datetime.now().strftime("%Y-%m-%d")
        path = self.staging_dir / f"staging-{day}.jsonl"
        with path.open("a", encoding="utf-8") as fh:
            for c in candidates:
                fh.write(
                    json.dumps(
                        {
                            **asdict(c),
                            "provenance": {
                                "origin": provenance.origin.value,
                                "observed_at": provenance.observed_at.isoformat(),
                                "source": provenance.source,
                                "session_id": provenance.session_id,
                            },
                        }
                    )
                    + "\n"
                )

    @staticmethod
    def memory_entry(candidate: MemoryCandidate, *, provenance: Provenance) -> str:
        parts = [f"- [{candidate.importance:.0f}] {candidate.content}"]
        if provenance.origin is Origin.OWNER:
            parts.append(f"  (by owner, {provenance.observed_at.date().isoformat()})")
        if candidate.target:
            parts.append(f"  (supersedes: {candidate.target})")
        return "\n".join(parts)