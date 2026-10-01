"""The kernel — the turn loop and the durability around it.

- `loop.py` — load a thread, run the nodes, pause for approval, save the thread.
- `threads.py` — SQLite by default, memory for tests, Postgres when that extra is installed.
- `events.py` — the stream the CLI, the API, and the bridge all read.
- `journal.py` — an append-only record of what a turn did.
- `turn.py` — exactly-once tool boundaries across a restart: replay a settled
  call, re-enter an interrupted one, and refuse to guess about a side effect
  that may or may not have run.
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
