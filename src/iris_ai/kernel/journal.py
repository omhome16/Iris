"""The turn journal — what happened, in order, durably.

A trace is for reading; a journal is for *resuming*. The difference is the
question it answers: a trace says "here is what the turn did", a journal says
"here is what the turn had done before it stopped", which is the only way a tool
boundary can be exactly-once across a crash.

One append-only JSONL file, one line per event, `fsync`-free (the OS makes the
ordering guarantees a single process needs, and an `fsync` per tool call would tax
every turn to protect against a power cut this project does not claim to
survive). What it does claim: a process that is killed mid-tool leaves a
`tool_start` with no `tool_end`, and that is a recoverable, *visible* state rather
than a guess.

Event kinds:

| event | meaning |
|---|---|
| `turn_start` | a turn began, with the prompt version and tool-surface digest |
| `tool_start` | a tool call began; carries the argument digest and whether it side-effects |
| `tool_end` | it finished: `ok` / `error`, and the reply, so a replay need not re-run it |
| `tool_interrupt` | it paused for the owner's approval — *not* a crash, and the resume re-enters it |
| `approval_granted` | an approval was spent, so the grant survives a restart |

There is deliberately **no `turn_end`**. What a resume needs to know is whether a
*step* settled, and that is a property of the step; a turn-level "finished" flag
would be a second thing to keep true, and it would be wrong the moment a turn
paused for an approval (which is not finishing) unless the graph learned to
distinguish the two on every exit path. `unfinished()` asks the question that
matters instead: which turns have a step that started and never settled.

The reply is stored because the alternative — re-running the tool to recover its
result — is a write nobody asked for twice. A journaled reply is what makes
"exactly once" a property of the file rather than a hope about the tool.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger("iris.journal")

#: Statuses a step can be left in. `started` with no end is the only ambiguous one,
#: and `iris_ai.kernel.turn` is the thing that decides what to do about it.
STARTED = "started"
DONE = "done"
FAILED = "failed"
INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class Step:
    """One tool call, as the journal saw it."""

    call_id: str
    tool: str
    digest: str = ""
    side_effecting: bool = True
    status: str = STARTED
    result: str = ""
    ok: bool = True
    error: str = ""

    @property
    def settled(self) -> bool:
        """Whether the outcome is known: replayed, or refused as uncertain."""
        return self.status in (DONE, FAILED)

    @property
    def waiting(self) -> bool:
        """Paused on an approval — the resume is expected to re-enter this step."""
        return self.status == INTERRUPTED


@dataclass
class TurnRecord:
    """One thread's journal entries, folded into the state a resume needs."""

    steps: dict[str, Step] = field(default_factory=dict)
    prompt_version: str = ""
    tools_digest: str = ""

    def pending(self) -> list[Step]:
        """Steps that started and never settled — the crash-mid-tool case."""
        return [s for s in self.steps.values() if s.status == STARTED]


@dataclass(frozen=True, slots=True)
class Event:
    at: str
    thread: str
    turn: str
    kind: str
    call_id: str = ""
    tool: str = ""
    digest: str = ""
    side_effecting: bool = True
    ok: bool = True
    error: str = ""
    result: str = ""
    detail: dict = field(default_factory=dict)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TurnJournal:
    """Append-only, readable, and prunable. The file is the source of truth.

    Constructed once per process (the engine holds it on the `Runtime`), it is
    safe for concurrent appends within one event loop — the writes are small and
    synchronous, which is what makes them ordered.
    """

    def __init__(self, path: Path, *, max_bytes: int | None = None, keep_turns: int = 50) -> None:
        self.path = Path(path)
        self.max_bytes = max_bytes if max_bytes is not None else 4_000_000
        #: How many turns of history `recent_turns` offers a caller; the *file*
        #: holds more (until it outgrows `max_bytes`, then one generation is kept).
        self.keep_turns = keep_turns

    # ── writing ──────────────────────────────────────────────────────────
    def _append(self, event: Event) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate()
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dataclasses.asdict(event), ensure_ascii=False, default=str) + "\n")

    def _rotate(self) -> None:
        """One generation, like the traces: a journal that grows forever is a bug."""
        try:
            if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
                old = self.path.with_suffix(".jsonl.1")
                if old.exists():
                    old.unlink()
                self.path.rename(old)
        except OSError as exc:  # pragma: no cover - filesystem-specific
            log.warning("could not rotate the journal: %s", exc)

    def turn_start(self, thread: str, turn: str, *, prompt_version: str = "", tools: str = "") -> None:
        self._append(
            Event(
                at=_now(),
                thread=thread,
                turn=turn,
                kind="turn_start",
                detail={"prompt_version": prompt_version, "tools_digest": tools},
            )
        )

    def tool_start(
        self,
        thread: str,
        turn: str,
        *,
        call_id: str,
        tool: str,
        digest: str = "",
        side_effecting: bool = True,
    ) -> None:
        self._append(
            Event(
                at=_now(),
                thread=thread,
                turn=turn,
                kind="tool_start",
                call_id=call_id,
                tool=tool,
                digest=digest,
                side_effecting=side_effecting,
            )
        )

    def tool_end(self, thread: str, turn: str, *, call_id: str, ok: bool, result: str, error: str = "") -> None:
        self._append(
            Event(
                at=_now(),
                thread=thread,
                turn=turn,
                kind="tool_end",
                call_id=call_id,
                ok=ok,
                result=result,
                error=error,
            )
        )

    def tool_interrupt(self, thread: str, turn: str, *, call_id: str) -> None:
        """A step paused for approval. Distinct from a crash, and the difference matters."""
        self._append(Event(at=_now(), thread=thread, turn=turn, kind="tool_interrupt", call_id=call_id))

    def approval_granted(self, thread: str, turn: str, *, call_id: str, action: str = "") -> None:
        """Record a spent approval, so a restart cannot forget and re-grant it."""
        self._append(
            Event(
                at=_now(),
                thread=thread,
                turn=turn,
                kind="approval_granted",
                call_id=call_id,
                detail={"action": action},
            )
        )

    # ── reading ──────────────────────────────────────────────────────────
    def events(self) -> list[Event]:
        """Every event, oldest first. A corrupt line is skipped, not fatal."""
        for path in (self.path.with_suffix(".jsonl.1"), self.path):
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    log.warning("skipping a corrupt journal line in %s", path)
                    continue
                if not isinstance(data, dict) or "thread" not in data or "kind" not in data:
                    continue
                known = {k: v for k, v in data.items() if k in Event.__dataclass_fields__}
                yield Event(**known)

    def fold(self) -> dict[tuple[str, str], TurnRecord]:
        """Every `(thread, turn)` in one pass. The primitive the readers share.

        One pass rather than one per query: `unfinished()` asking `turn()` for
        each pair would re-read the whole file per pair, which is the kind of
        thing that is invisible until a journal has ten thousand lines.
        """
        records: dict[tuple[str, str], TurnRecord] = {}
        for event in self.events():
            key = (event.thread, event.turn)
            if event.kind == "turn_start":
                record = records.setdefault(key, TurnRecord())
                record.prompt_version = str(event.detail.get("prompt_version", ""))
                record.tools_digest = str(event.detail.get("tools_digest", ""))
                continue
            record = records.get(key)
            if record is None:
                # An event for a turn whose start rotated away: still worth
                # keeping, since it is what a crash left behind.
                record = records.setdefault(key, TurnRecord())
            if event.kind == "tool_start":
                record.steps[event.call_id] = Step(
                    call_id=event.call_id,
                    tool=event.tool,
                    digest=event.digest,
                    side_effecting=event.side_effecting,
                )
            elif event.kind in ("tool_end", "tool_interrupt"):
                step = record.steps.get(event.call_id)
                if step is None:  # an end with no start: nothing to update
                    continue
                if event.kind == "tool_interrupt":
                    record.steps[event.call_id] = replace(step, status=INTERRUPTED)
                else:
                    record.steps[event.call_id] = replace(
                        step,
                        status=DONE if event.ok else FAILED,
                        result=event.result,
                        ok=event.ok,
                        error=event.error,
                    )
        return records

    def turn(self, thread: str, turn: str = "") -> TurnRecord:
        """Fold one thread's events (or one turn of it) into a `TurnRecord`.

        A thread with several turns folds them together, which is what a resume
        wants: the last value of each field wins, and every settled step is
        visible to the boundary.
        """
        record = TurnRecord()
        for (thread_name, turn_name), folded in self.fold().items():
            if thread_name != thread or (turn and turn_name != turn):
                continue
            record.steps.update(folded.steps)
            record.prompt_version = folded.prompt_version or record.prompt_version
            record.tools_digest = folded.tools_digest or record.tools_digest
        return record

    def granted(self, thread: str, call_id: str) -> bool:
        """Was this approval already spent? Survives a restart, which is the point."""
        if not call_id:
            return False
        return any(
            event.kind == "approval_granted" and event.thread == thread and event.call_id == call_id
            for event in self.events()
        )

    def unfinished(self) -> list[tuple[str, str]]:
        """`(thread, turn)` pairs with a step that started and never settled.

        What a boot reports: a killed process leaves these, and the owner deserves
        to know a tool may or may not have run rather than discovering it later.
        """
        return [key for key, record in self.fold().items() if record.pending()]


__all__ = [
    "DONE",
    "FAILED",
    "INTERRUPTED",
    "STARTED",
    "Event",
    "Step",
    "TurnJournal",
    "TurnRecord",
]
