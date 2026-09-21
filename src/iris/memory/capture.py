"""Capture — the safety net that keeps the episodic tier from starving.

Why this exists
---------------
The memory-orchestration v2 design removed the per-turn extraction judge and
handed the "is this worth keeping?" decision to the agent's own `note` tool
(`docs/superpowers/specs/2026-08-20-memory-orchestration-v2.md`, §4.2). The
reasoning was sound — write-time reconciliation is the expensive, error-prone
part — but the outcome was measured and it failed: **across 36 traced turns the
agent called `note` zero times**. Leaving curation to a model's goodwill, with
no feedback signal when it declines, means `MEMORY.md` grows only through
compaction flush and explicit `remember`, and the whole promotion pipeline runs
on empty.

This module restores the write volume *without* restoring the v2 design's
problems, by keeping the two concerns apart:

- **Code decides whether to spend a judgment** (`worth_capturing`): a
  deterministic prefilter, so trivial turns ("ok", "thanks") still cost zero
  model calls — the actual win v2 was protecting.
- **A model decides whether the turn holds a durable fact** — one JEV request
  when configured, otherwise one cheap-tier JSON call. Either way it is
  judgement, not reconciliation: ADD-only, agent provenance, no UPDATE/DELETE.
- **Curation still happens only in dreaming.** A capture is evidence in the
  daily note, stamped `(note)`, and must still clear the deterministic Light-phase
  gate to reach `MEMORY.md`. This module cannot write curated memory.

Recall-loop prevention is handled structurally rather than by prompt: the
judgment sees the assembled context (USER.md + MEMORY.md, the same text the
agent sees) and is asked whether the fact is *already in it*. A fact recalled a
hundred times still enters the daily note once.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from iris.config import settings
from iris.jev.client import JevClient, noul, score

log = logging.getLogger("iris.capture")

# ── deterministic prefilter ─────────────────────────────────────────────────

_ACKS = frozenset(
    {
        "hi", "hey", "hello", "yo", "sup", "hiya", "morning", "evening",
        "thanks", "thank you", "thanks!", "thx", "ty", "cheers",
        "ok", "okay", "k", "kk", "cool", "nice", "great", "good", "fine",
        "yes", "no", "yeah", "yep", "nope", "sure", "maybe", "nvm",
        "lol", "haha", "hmm", "hm", "test", "ping", "bye", "goodnight",
        "good night", "gn", "you there", "u there", "hello?", "hi?",
    }
)

# A turn needs a first-person signal to be about the owner...
_PERSONAL_CUES = (
    "i ", "i'", "i’", "im ", "i'm", "i’m", "i've", "i’ve", "i'd", "i'd", "i’ll", "i'll",
    "my ", "me ", " mine", "myself", "we ", "our ", "ours", "us ",
)

# ...and a durability signal to be worth remembering rather than transient.
_DURABLE_CUES = (
    "prefer", "like", "love", "hate", "dislike", "favourite", "favorite",
    "always", "never", "usually", "normally", "every day", "every week",
    "remember", "remind", "note that", "for the record",
    "my name", "call me", "i work", "i live", "i'm based", "i am based",
    "i use", "i'm using", "i am using", "my job", "my role", "my team",
    "decided", "plan to", "planning to", "goal", "project", "deadline",
    "birthday", "anniversary", "allerg", "vegan", "vegetarian", "intolerant",
    "lease", "apartment", "mortgage", "landlord", "rent",
    "wife", "husband", "partner", "girlfriend", "boyfriend", "son", "daughter",
    "mother", "father", "brother", "sister", "parents", "family",
    "doctor", "medication", "diagnos", "therapy",
    "meeting", "schedule", "timezone", "time zone", "commute", "flight",
    "password", "account", "subscription", "invoice", "salary",
)

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'\-]{2,}")
_STOPWORDS = frozenset(
    ["the", "and", "for", "that", "with", "this", "from", "have", "has", "had", "was", "were", "are", "is", "be", "been", "being", "you", "your", "but", "not", "all", "any", "some", "just", "very", "much", "more", "most", "about", "into", "over", "under", "again", "then", "than", "what", "when", "where", "which", "who", "whom", "how", "why", "will", "would", "can", "could", "should", "shall", "may", "might", "i'm", "i've", "i'll", "they", "them", "their", "there", "here", "also", "because", "while", "during", "before", "after", "always", "never", "usually", "normally", "really", "actually", "often", "sometimes", "every", "thing", "things", "something", "anything", "maybe", "probably", "today", "tomorrow", "yesterday", "prefer", "likes", "like", "love", "hate", "remember", "remind", "decided", "plan", "plans", "planning", "want", "need", "think", "know", "going", "keep"]
)


def worth_capturing(user_message: str, ai_reply: str = "") -> bool:
    """Deterministic prefilter: is this turn a candidate for capture at all?

    Deliberately conservative — it only decides whether to *spend a judgment*.
    A false negative costs one un-noted turn (recoverable: the same fact usually
    recurs); a false positive costs a model call, not a memory.
    """
    if not settings.capture_enabled:
        return False
    text = (user_message or "").strip()
    if not ai_reply or len(text) < settings.capture_min_chars:
        return False
    folded = text.casefold().strip()
    if folded in _ACKS:
        return False
    # A bare question is not a fact, unless it also states something personal.
    has_personal = any(cue in folded for cue in _PERSONAL_CUES)
    if folded.endswith("?") and not has_personal:
        return False
    if not has_personal:
        return False
    return any(cue in folded for cue in _DURABLE_CUES)


def condense(text: str, *, max_chars: int = 300) -> str:
    """The owner's own words, trimmed — never generated.

    Jev returns no text, so when it supplies the judgment this function supplies
    the *content*. Copying the owner's sentence verbatim is the honest choice:
    it cannot hallucinate, and the Light phase still scores it before promotion.
    """
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rsplit(" ", 1)[0] + "…"


def lexical_triggers(text: str, *, limit: int = 4) -> list[str]:
    """Distinctive words for the Light phase's trigger-diversity signal."""
    words = [w.casefold() for w in _WORD_RE.findall(text or "")]
    picks: list[str] = []
    for word in sorted(set(words), key=len, reverse=True):
        if word in _STOPWORDS or len(word) < 5:
            continue
        picks.append(word)
        if len(picks) >= limit:
            break
    return picks


# ── the judgment ────────────────────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class CaptureResult:
    captured: bool = False
    fact: str = ""
    importance: float = 0.0
    triggers: tuple[str, ...] = ()
    reason: str = ""


_LLM_SYSTEM = """You are Iris's capture pass — the safety net behind her memory.

Given one conversation turn, decide whether the owner revealed a DURABLE fact
about themselves worth keeping: a preference, decision, plan, relationship,
project, constraint, or stable attribute.

Say no for: small talk, transient state ("I'm tired"), questions, anything
already present in the supplied context, and anything the assistant merely
said. When in doubt, say no — a fact that matters will recur.

Respond ONLY with JSON:
{"capture": true|false, "fact": "<present-tense, self-contained, <300 chars>",
 "importance": 1-10, "triggers": ["2-5 short phrases that would cue this later"]}"""


def _importance_from_score(level: float) -> float:
    """Map Jev's 0..3 ordered levels onto the 1..10 importance scale."""
    idx = max(0, min(3, round(level)))
    return (3.0, 5.0, 7.0, 9.0)[idx]


async def judge_capture(
    llm: Any,
    jev: JevClient | None,
    *,
    user_message: str,
    ai_reply: str,
    known_context: str = "",
) -> CaptureResult:
    """Decide whether this turn holds a new durable fact. Never raises."""
    if jev is not None and jev.enabled:
        result = await _judge_with_jev(jev, user_message, ai_reply, known_context)
        if result is not None:
            return result
        # Jev answered nothing usable — fall through to the cheap model.
    if llm is None or not settings.capture_use_llm:
        return CaptureResult(reason="no model available for the capture judgment")
    return await _judge_with_llm(llm, user_message, ai_reply, known_context)


async def _judge_with_jev(
    jev: JevClient, user_message: str, ai_reply: str, known_context: str
) -> CaptureResult | None:
    state = {
        "turn": {
            "owner": condense(user_message, max_chars=2000),
            "assistant": condense(ai_reply, max_chars=1200),
        },
        # What the agent already sees this turn — the recall-loop guard.
        "already_in_context": condense(known_context, max_chars=int(settings.capture_context_chars)),
    }
    questions = {
        "durable": noul(
            "Does `turn.owner` state a durable fact about the owner that is worth remembering "
            "long-term — a preference, decision, plan, relationship, project, or stable attribute? "
            "Judge only what the owner said, not what the assistant said.",
            true="It states something durable about the owner.",
            false="It is small talk, a question, or transient state.",
        ),
        "already_known": noul(
            "Is that fact already stated in `already_in_context`?",
            true="The context already contains this fact.",
            false="The context does not contain this fact.",
        ),
        "importance": score(
            "If Iris had to choose what to remember six months from now, how important is `turn.owner`?",
            [
                "Forgettable: pleasant but inconsequential.",
                "Useful context: worth knowing, not critical.",
                "Important: shapes how Iris should behave toward the owner.",
                "Critical: core to the owner's life, safety, or identity.",
            ],
        ),
    }
    answers = await jev.ask(state, questions)
    if answers is None:
        return None
    durable = answers.noul("durable")
    already = answers.noul("already_known")
    importance = _importance_from_score(answers.score("importance"))
    if durable < settings.capture_gate:
        return CaptureResult(reason=f"durable {durable:.2f} below gate {settings.capture_gate:.2f}")
    if already >= settings.capture_already_known_gate:
        return CaptureResult(reason=f"already in context ({already:.2f})")
    if importance < settings.capture_min_importance:
        return CaptureResult(reason=f"importance {importance:.0f} below floor")
    fact = condense(user_message)
    if not fact:
        return CaptureResult(reason="nothing to condense")
    return CaptureResult(
        captured=True,
        fact=fact,
        importance=importance,
        triggers=tuple(lexical_triggers(user_message)),
        reason=f"jev: durable={durable:.2f} already={already:.2f}",
    )


async def _judge_with_llm(
    llm: Any, user_message: str, ai_reply: str, known_context: str
) -> CaptureResult:
    context_block = condense(known_context, max_chars=int(settings.capture_context_chars))
    try:
        raw = await llm.complete(
            [
                {"role": "system", "content": _LLM_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"## Already in Iris's context\n{context_block or '(nothing)'}\n\n"
                        f"## Owner said\n{condense(user_message, max_chars=2000)}\n\n"
                        f"## Iris replied\n{condense(ai_reply, max_chars=1200)}\n"
                    ),
                },
            ],
            tier="cheap",
            json_mode=True,
            max_tokens=400,
            max_attempts=1,
            timeout=45.0,
        )
        data = json.loads(raw)
    except Exception as exc:  # noqa: BLE001 - capture must never break a turn
        log.warning("capture judgment skipped: %s", exc)
        return CaptureResult(reason="cheap-model judgment failed")
    if not isinstance(data, dict) or not data.get("capture"):
        return CaptureResult(reason="model declined")
    fact = condense(str(data.get("fact", "")).strip())
    if len(fact) < 12:
        return CaptureResult(reason="model returned no usable fact")
    try:
        importance = float(data.get("importance", 5.0))
    except (TypeError, ValueError):
        importance = 5.0
    importance = max(1.0, min(10.0, importance))
    if importance < settings.capture_min_importance:
        return CaptureResult(reason=f"importance {importance:.0f} below floor")
    triggers = [str(t).strip() for t in (data.get("triggers") or []) if str(t).strip()][:5]
    return CaptureResult(
        captured=True,
        fact=fact,
        importance=importance,
        triggers=tuple(triggers or lexical_triggers(fact)),
        reason="cheap-model judgment",
    )


def note_line(result: CaptureResult) -> str:
    """The daily-note line the Light phase parses back out (see `_NOTE_LINE_RE`)."""
    entry = f"- [{result.importance:.0f}] {result.fact}"
    if result.triggers:
        entry += f" (triggers: {', '.join(result.triggers)})"
    return f"{entry} (note)"
