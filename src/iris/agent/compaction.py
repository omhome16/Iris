"""Compaction — keep conversation history inside a working budget.

Pattern (Pi/Claude Code lineage): when the serialized history exceeds the
trigger budget, run a silent compaction turn that

1. **Memory flush** — a cheap-model turn extracts durable facts from the
   conversation and appends them to the daily note (episodic tier). Nothing
   is lost when history is dropped.
2. **Summarize** — the same turn produces a compact summary, stored in state
   and injected by the agent node as a system block.
3. **Trim** — history is cut back to the keep-budget, and the cut happens
   only at complete-turn boundaries: tool results are never orphaned from
   their calls (Pi's `keepRecentTokens` + pair-repair pattern).

The flush/summary call is best-effort — on any provider failure the trim
still happens, just without a summary.
"""

from __future__ import annotations

import json
import logging

from iris.config import settings
from iris.memory.chunking import estimate_tokens
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient

log = logging.getLogger("iris.compaction")

_FLUSH_SYSTEM = """You are Iris's compaction pass. Given the conversation below:
1. Extract durable facts about the owner's life worth keeping — preferences,
   decisions, plans, relationships, projects. Short, self-contained, present
   tense. Do NOT include small talk or transient state.
2. Write a compact summary (2-4 sentences) covering the whole conversation.

Respond ONLY with JSON: {"facts": ["..."], "summary": "..."}"""


def _msg_tokens(m: object) -> int:
    content = getattr(m, "content", "")
    if isinstance(content, list):
        content = " ".join(
            str(p.get("text", "")) for p in content if isinstance(p, dict)
        )
    total = estimate_tokens(str(content or ""))
    for tc in getattr(m, "tool_calls", None) or []:
        total += estimate_tokens(json.dumps(tc.get("args", {})))
    return total


def messages_tokens(messages: list) -> int:
    """Serialized token estimate of a message history (word-count based)."""
    return sum(_msg_tokens(m) for m in messages)


def trim_messages(messages: list, keep_tokens: int) -> list:
    """Drop the oldest messages until the tail fits `keep_tokens`.

    The cut is repaired at the front: any orphaned tool results (whose
    calling ai message was dropped) are skipped, so the kept window always
    starts at a complete turn boundary.
    """
    if not messages:
        return messages
    counts = [_msg_tokens(m) for m in messages]
    total = 0
    cutoff = len(messages)
    for i in range(len(messages) - 1, -1, -1):
        total += counts[i]
        if total > keep_tokens:
            cutoff = i + 1
            break
    else:
        return messages  # everything already fits — nothing to trim
    while cutoff < len(messages) and getattr(messages[cutoff], "type", "") == "tool":
        cutoff += 1
    # The front of the kept window must not be an AI message whose tool
    # results were cut away: its dangling tool_calls make the provider
    # (and LangGraph's message validation) choke on the next turn.
    if cutoff < len(messages) and getattr(messages[cutoff], "type", "") == "ai" \
            and getattr(messages[cutoff], "tool_calls", None):
        cutoff += 1
        while cutoff < len(messages) and getattr(messages[cutoff], "type", "") == "tool":
            cutoff += 1
    return messages[cutoff:]


def _serialize(messages: list) -> str:
    lines: list[str] = []
    for m in messages:
        role = getattr(m, "type", "")
        content = getattr(m, "content", "")
        if isinstance(content, list):
            content = " ".join(
                str(p.get("text", "")) for p in content if isinstance(p, dict)
            )
        if role == "tool":
            continue
        if role == "ai":
            calls = getattr(m, "tool_calls", None) or []
            if calls:
                names = ", ".join(tc.get("name", "") for tc in calls)
                lines.append(f"Iris: [tool call: {names}] {content or ''}".strip())
                continue
        label = "Owner" if role == "human" else "Iris"
        lines.append(f"{label}: {content}")
    return "\n\n".join(lines)


async def compact_turn(llm: LLMClient, files: WorkspaceFiles, messages: list) -> str:
    """Flush durable facts to the daily note; return the summary (capped).

    Best-effort: returns "" on any failure so the caller can still trim.
    """
    try:
        payload = await llm.complete(
            [
                {"role": "system", "content": _FLUSH_SYSTEM},
                {"role": "user", "content": _serialize(messages)},
            ],
            tier="cheap",
            json_mode=True,
            max_tokens=800,
            max_attempts=2,
            timeout=60.0,
        )
        data = json.loads(payload)
    except Exception as exc:  # noqa: BLE001 - compaction must never crash the turn
        log.warning("compaction flush failed: %s", exc)
        return ""
    facts = [str(f).strip() for f in data.get("facts", []) if str(f).strip()]
    summary = str(data.get("summary", "")).strip()
    if facts:
        try:
            files.append_daily("Compaction flush:\n" + "\n".join(f"- {f}" for f in facts))
        except Exception as exc:  # noqa: BLE001
            log.warning("compaction daily-note flush failed: %s", exc)
    cap = settings.compaction_summary_tokens
    words = summary.split()
    return " ".join(words[:cap])
