"""Reflection pass — post-turn hallucination triage on retrieval-backed turns.

When the agent actually *retrieved* memory this turn (a memory_search tool
result exists), a cheap-model pass re-checks the reply's factual claims
against the retrieved excerpts and appends any unsupported claims to
`workspace/config/hallucination_flags.jsonl` (append-only telemetry, surfaced
in /mind). Discipline: the pass is best-effort — it must never raise, never
block the reply, and never touch the memory files themselves.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from iris.memory.llm import LLMClient

log = logging.getLogger("iris.reflection")

_SYSTEM = (
    "You are a fact-checking reviewer. Compare the assistant's reply against "
    "the retrieved memory excerpts it was given. Flag every factual claim "
    "(names, dates, numbers, personal facts, events) that the excerpts do NOT "
    "support — either because it contradicts them or because it invents "
    "details. Return JSON only: "
    '{"flagged": [{"claim": "...", "why": "..."}]} '
    'or {"flagged": []} if everything checks out. Do not flag opinions or '
    "hedged language."
)


class ReflectionPass:
    """Appends hallucination flags for retrieval-backed turns."""

    def __init__(self, llm: LLMClient, path: Path) -> None:
        self.llm = llm
        self.path = path

    async def check(self, *, user_message: str, ai_reply: str, retrieved: list[str]) -> None:
        """Run the triage only when the turn actually used retrieval."""
        if not retrieved:
            log.debug("reflection skipped: no retrieval this turn")
            return
        try:
            excerpts = "\n\n".join(f"[{i + 1}] {t[:600]}" for i, t in enumerate(retrieved))
            payload = await self.llm.complete(
                [
                    {"role": "system", "content": _SYSTEM},
                    {
                        "role": "user",
                        "content": (
                            f"## Retrieved memory excerpts\n{excerpts}\n\n"
                            f"## User message\n{user_message}\n\n"
                            f"## Iris's reply\n{ai_reply}\n"
                        ),
                    },
                ],
                tier="cheap",
                json_mode=True,
                max_tokens=600,
                max_attempts=1,
                timeout=45.0,
            )
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                log.warning("reflection: unparseable verdict, skipping")
                return
            flags = [f for f in data.get("flagged", []) if str(f.get("claim", "")).strip()]
            if not flags:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                for f in flags:
                    line = {
                        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                        "claim": str(f.get("claim"))[:500],
                        "why": str(f.get("why"))[:300],
                        "reply_excerpt": ai_reply[:200],
                    }
                    fh.write(json.dumps(line) + "\n")
            log.info("reflection flagged %d unsupported claim(s)", len(flags))
        except Exception as exc:  # noqa: BLE001 - reflection must never fail the turn
            log.warning("reflection pass skipped: %s", exc)

    def count(self) -> int:
        if not self.path.exists():
            return 0
        try:
            return sum(
                1
                for line in self.path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        except OSError as exc:  # pragma: no cover
            log.warning("reflection count failed: %s", exc)
            return 0


_RETRIEVAL_TOOLS = ("memory_search", "deep_dive")


def _tool_call_names(state: dict) -> dict[str, str]:
    """tool_call_id → tool name, resolved from the AI messages that issued
    the calls. More robust than reading `.name` off each tool message, which
    can be lost through checkpoint serialization."""
    names: dict[str, str] = {}
    for m in state.get("messages", []):
        for tc in getattr(m, "tool_calls", None) or []:
            cid = tc.get("id")
            if cid:
                names[cid] = tc.get("name", "")
    return names


def retrieved_excerpts(state: dict) -> list[str]:
    """Tool-result contents of *memory retrieval* calls in this turn's
    messages. Only memory_search/deep_dive results are evidence the reply can
    be checked against — file reads, web searches and other tools are not
    memory, and running the reflection pass over them produced token waste
    and false positive flags."""
    names = _tool_call_names(state)
    out: list[str] = []
    for m in state.get("messages", []):
        if getattr(m, "type", "") != "tool":
            continue
        name = getattr(m, "name", "") or names.get(getattr(m, "tool_call_id", ""), "")
        if name in _RETRIEVAL_TOOLS:
            out.append(str(m.content))
    return [t for t in out if t][-6:]
