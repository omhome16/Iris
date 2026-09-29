"""The kernel — durability around a turn, not (yet) a replacement for the graph.

`09-roadmap.md`'s Phase 5 describes a full turn state machine with LangGraph
optional, which is the largest change on the roadmap. What is here is the part
that has to exist *before* that swap can be safe, and that is worth having on its
own either way:

- `journal.py` — an append-only record of what a turn did: tool starts, ends,
  interrupts, spent approvals, and the prompt/tool version the turn ran with.
- `turn.py` — the tool boundary, which turns that record into exactly-once
  behaviour across a restart: replay a settled call, re-enter an interrupted one,
  and refuse to *guess* about a side-effecting call that may or may not have run.

The graph stays the orchestrator, so a later re-architecture is a swap rather
than a rewrite — but the ambiguity a crash introduces is resolved in code, with
tests, instead of being left to hope.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.kernel.journal import TurnJournal, TurnRecord
from iris_ai.kernel.turn import Decision, TurnKernel, side_effecting, tools_digest

__all__ = [
    "Decision",
    "TurnJournal",
    "TurnKernel",
    "TurnRecord",
    "build_journal",
    "side_effecting",
    "tools_digest",
]


def build_journal(root: Path, *, max_bytes: int | None = None) -> TurnJournal:
    """The journal for this process, beside the other runtime state.

    `max_bytes` exists for tests and for a deployment that wants a smaller
    footprint; the default keeps a bounded file with one generation, the same way
    the traces do.
    """
    path = Path(root) / "config" / "journal.jsonl"
    return TurnJournal(path) if max_bytes is None else TurnJournal(path, max_bytes=max_bytes)
