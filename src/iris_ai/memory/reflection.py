"""Reflection pass — post-turn hallucination triage on retrieval-backed turns.

When the agent actually *retrieved* memory this turn (a memory_search tool
result exists), a pass re-checks the reply's factual claims against the
retrieved excerpts and appends any unsupported claims to
`workspace/config/hallucination_flags.jsonl` (append-only telemetry, surfaced
in /mind). Discipline: the pass is best-effort — it must never raise, never
block the reply, and never touch the memory files themselves.

**Two implementations, one decision.** The question "is this sentence
supported by these excerpts?" is a grounded yes/no judgment over supplied text,
which is exactly what a System One model answers in one request — so JEV goes
first and the cheap-tier LLM is the fallback (the same order P4 established for
the capture gate). The JEV path has two properties the model path does not:

- **It is inspectable.** Every sentence gets a probability, and those land in
  the turn trace, so a flag is a number with a threshold rather than an opaque
  list the model chose to emit.
- **It is bounded.** The claims are the reply's sentences, split
  deterministically, and all of them go in *one* batched request; the model
  path has to be trusted to enumerate the claims itself.

Token cost is the input side only ($0.042/Mtok) and the request count is one per
turn either way — the win is that no completion is billed, and the latency win
is the LLM round trip the model path would have paid in the background.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iris_ai import turnlog
from iris_ai.config import settings
from iris_ai.jev.client import noul
from iris_ai.memory.llm import LLMClient

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

# Sentence split for the JEV path. Deliberately crude and deterministic: a
# sentence is a claim-sized unit, and the list has to be identical between runs
# for the same reply or the recorded probabilities stop meaning anything.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_MIN_CLAIM_CHARS = 25


def _claims(reply: str, *, limit: int | None = None) -> list[str]:
    """The reply split into claim-sized sentences, capped and deduplicated.

    Very short fragments are dropped: "Yes." or "Sure!" carries no fact to
    check, and asking about it only spends input tokens.
    """
    limit = limit if limit is not None else int(settings.jev_reflection_max_claims)
    out: list[str] = []
    seen: set[str] = set()
    for raw in _SENTENCE_RE.split(reply or ""):
        text = " ".join(raw.split())
        if len(text) < _MIN_CLAIM_CHARS:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max(1, limit):
            break
    return out


class ReflectionPass:
    """Appends hallucination flags for retrieval-backed turns."""

    def __init__(
        self,
        llm: LLMClient,
        path: Path,
        *,
        jev: Any | None = None,
        threshold: float | None = None,
    ) -> None:
        self.llm = llm
        self.path = path
        self.jev = jev
        self.threshold = (
            threshold if threshold is not None else float(settings.jev_reflection_threshold)
        )

    @property
    def jev_enabled(self) -> bool:
        return bool(
            self.jev is not None
            and settings.jev_reflection_enabled
            and getattr(self.jev, "enabled", False)
        )

    async def check(self, *, user_message: str, ai_reply: str, retrieved: list[str]) -> None:
        """Run the triage only when the turn actually used retrieval."""
        if not retrieved:
            log.debug("reflection skipped: no retrieval this turn")
            return
        try:
            if self.jev_enabled:
                flags, mode = await self._flags_with_jev(ai_reply, retrieved)
                if flags is not None:
                    self._write(flags, ai_reply)
                    # `reflection_decision`, not `reflection`: the graph records
                    # *where* the pass ran (background/inline) under the latter,
                    # and two events of the same kind meaning different things
                    # would make the trace unreadable.
                    turnlog.record(
                        "reflection_decision",
                        mode=mode,
                        claims=len(_claims(ai_reply)),
                        flagged=len(flags),
                    )
                    return
                # JEV answered nothing usable — the model path still can.
            flags = await self._flags_with_llm(user_message, ai_reply, retrieved)
            if flags is None:
                return
            self._write(flags, ai_reply)
            turnlog.record("reflection_decision", mode="llm", flagged=len(flags))
        except Exception as exc:  # noqa: BLE001 - reflection must never fail the turn
            log.warning("reflection pass skipped: %s", exc)

    # ── JEV path ─────────────────────────────────────────────────────────

    async def _flags_with_jev(
        self, ai_reply: str, retrieved: list[str]
    ) -> tuple[list[dict[str, str]] | None, str]:
        """One batched request: a support probability per claim sentence.

        Returns `(flags, mode)` — or `(None, reason)` when JEV could not answer,
        so the caller falls back to the model.
        """
        claims = _claims(ai_reply)
        if not claims:
            return [], "jev:no-claims"
        state = {
            "excerpts": [t[:600] for t in retrieved if t][:6],
            "claims": [{"id": i, "text": text} for i, text in enumerate(claims)],
        }
        questions = {
            f"c{i}": noul(
                f"Does `excerpts` support `claims[{i}].text`? Answer true only if the "
                "excerpts state this claim (or something entailing it). Details the "
                "excerpts do not mention count as unsupported.",
                true="The excerpts support this claim.",
                false="The excerpts do not support this claim.",
            )
            for i in range(len(claims))
        }
        answers = await self.jev.ask(state, questions)
        if answers is None:
            return None, "jev-failed"

        flags: list[dict[str, str]] = []
        probabilities: list[dict[str, Any]] = []
        for i, claim in enumerate(claims):
            probability = answers.noul(f"c{i}", default=1.0)
            probabilities.append({"claim": claim[:120], "support": round(probability, 3)})
            if probability < self.threshold:
                flags.append(
                    {
                        "claim": claim,
                        "why": f"JEV support p={probability:.2f} below {self.threshold:.2f}",
                    }
                )
        # The judgment is recorded whether or not it flagged anything: a
        # decision nobody can inspect is indistinguishable from one that ran
        # and did nothing.
        turnlog.record(
            "reflection_claims",
            model=answers.model,
            threshold=self.threshold,
            probabilities=probabilities,
        )
        return flags, "jev"

    # ── model fallback ───────────────────────────────────────────────────

    async def _flags_with_llm(
        self, user_message: str, ai_reply: str, retrieved: list[str]
    ) -> list[dict[str, str]] | None:
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
            return None
        return [f for f in data.get("flagged", []) if str(f.get("claim", "")).strip()]

    # ── the write ────────────────────────────────────────────────────────

    def _write(self, flags: list[dict[str, str]], ai_reply: str) -> None:
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
