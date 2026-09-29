"""The tool boundary — where a replayed turn stops being a re-run.

A journal alone is a record. This is the decision that makes the record *useful*:
given a tool call that is about to happen, one of three things is true, and they
are not interchangeable.

- **It already happened** (`replay`). The journal holds the reply, so the tool is
  not called again and the model receives exactly what it received the first time.
  This is the ordinary case after a resume: LangGraph re-enters the tools node
  from the top, and every call in it that already settled must come back from the
  journal rather than from the world.
- **It never happened** (`run`). Nothing recorded, or recorded as interrupted
  waiting for an approval the owner has since given — which is a *pause*, not a
  crash, and the resume is supposed to re-enter it.
- **It might have happened** (`uncertain`). A `tool_start` with no end and no
  interrupt means the process died between them. For a read-only tool that is
  harmless — re-running costs a lookup — so it runs. For a side-effecting tool it
  is the one case where no answer is honest: re-running risks doing it twice,
  skipping risks claiming it did not happen. Iris refuses, says so, and hands the
  owner the decision. **Exactly-once beats at-least-once, and "unknown" beats a
  confident guess.**

The boundary is deliberately a *pure decision about data* plus one journal write.
The graph stays the orchestrator; this does not replace it. What it replaces is
the assumption that a turn only runs once.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from iris_ai.approval import effective_digest

if TYPE_CHECKING:
    from iris_ai.kernel.journal import Step, TurnJournal

log = logging.getLogger("iris.kernel")

#: How a call should proceed. `replay` carries the journaled reply.
REPLAY = "replay"
RUN = "run"
UNCERTAIN = "uncertain"

UNCERTAIN_REPLY = (
    "this action may or may not have completed before Iris restarted, so it has "
    "not been repeated — repeating a side-effecting tool can do it twice. Check its "
    "effect (a file, a message, a record) and call it again if it did not happen"
)


def tools_digest(names: list[str] | tuple[str, ...]) -> str:
    """A stable digest of the tool surface a turn ran with.

    Recorded per turn so a trace and a journal agree about *what the model could
    reach* at the time — the same reason prompts carry a version. Sorted, so the
    digest is a function of the set and not of the order the registry happened to
    iterate in.
    """
    canonical = "|".join(sorted(names))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Decision:
    """What should happen to one tool call, and why."""

    action: str
    reason: str = ""
    reply: str = ""
    step: Step | None = None

    @property
    def replay(self) -> str | None:
        return self.reply if self.action == REPLAY else None

    @property
    def uncertain(self) -> bool:
        return self.action == UNCERTAIN


class TurnKernel:
    """The tool boundary, backed by the journal.

    Constructed per `ChatGraph`. Holds no per-call state of its own: everything it
    decides comes from the journal, which is what makes it survive the process.
    """

    def __init__(self, journal: TurnJournal | None = None) -> None:
        self.journal = journal

    def _thread(self, thread: str, turn: str) -> tuple[str, str]:
        return thread or "default", turn or "1"

    def before_tool(
        self,
        *,
        thread: str,
        turn: str,
        call_id: str,
        tool: str,
        args: object,
        side_effecting: bool = True,
    ) -> Decision:
        """Decide (and record the start) for one tool call.

        Recording the start is part of the decision, not a caller's job: a crash
        between "decided to run" and "recorded that we ran" is exactly the gap this
        exists to close, so the write happens here, before the caller dispatches.
        """
        _, turn = self._thread(thread, turn)
        if self.journal is None:
            return Decision(RUN, "no journal: nothing to replay from")
        record = self.journal.turn(thread, turn)
        step = record.steps.get(call_id) if call_id else None

        if step is not None and step.settled:
            return Decision(
                REPLAY,
                f"{step.tool} already ran this turn ({step.status}); the journaled reply is replayed",
                reply=step.result,
                step=step,
            )
        if step is not None and step.waiting:
            # Paused for approval, not killed: the resume is *meant* to re-enter.
            return Decision(RUN, "the step was waiting for approval and is being resumed", step=step)
        if step is not None and step.status == "started" and side_effecting:
            log.warning(
                "tool boundary: %s (%s) started and never settled — refusing to repeat it",
                tool,
                call_id,
            )
            return Decision(UNCERTAIN, UNCERTAIN_REPLY, step=step)

        self.journal.tool_start(
            thread, turn, call_id=call_id, tool=tool, digest=effective_digest(args), side_effecting=side_effecting
        )
        return Decision(RUN, "not in the journal: first attempt")

    def after_tool(self, *, thread: str, turn: str, call_id: str, ok: bool, reply: str, error: str = "") -> None:
        """Record the outcome — and the reply, so a replay never re-runs the tool."""
        if self.journal is None:
            return
        _, turn = self._thread(thread, turn)
        self.journal.tool_end(thread, turn, call_id=call_id, ok=ok, result=reply, error=error)

    def interrupted(self, *, thread: str, turn: str, call_id: str) -> None:
        """Record that the step paused for the owner rather than finishing."""
        if self.journal is None:
            return
        _, turn = self._thread(thread, turn)
        self.journal.tool_interrupt(thread, turn, call_id=call_id)

    def uncertain_reply(self) -> str:
        """The payload the model gets for a step that may or may not have run."""
        import json

        return json.dumps({"ok": False, "error": UNCERTAIN_REPLY, "uncertain": True}, ensure_ascii=False)

    # ── versioning ───────────────────────────────────────────────────────
    def turn_start(self, *, thread: str, turn: str, prompt_version: str, tools: list[str] | tuple[str, ...]) -> None:
        """Record what this turn ran with: which prompt, which reachable tools."""
        if self.journal is None:
            return
        _, turn = self._thread(thread, turn)
        self.journal.turn_start(thread, turn, prompt_version=prompt_version, tools=tools_digest(tools))

    # ── recovery ─────────────────────────────────────────────────────────
    def pending(self) -> list[tuple[str, str]]:
        """Turns a previous process left mid-tool — reported at boot, never hidden."""
        return [] if self.journal is None else self.journal.unfinished()


def side_effecting(name: str) -> bool:
    """Whether a tool call may change something outside Iris.

    Derived from the same class policy the guard chain uses, so "does this need an
    exactly-once boundary" and "does this need an approval" cannot drift apart: a
    class that may act on the world is a class that may act twice.
    """
    from iris_ai.toolpolicy import ToolClass
    from iris_ai.toolpolicy import resolve as resolve_tool_policy

    try:
        decision = resolve_tool_policy(name)
    except Exception:  # noqa: BLE001 - an undeclared tool is not a reason to skip the boundary
        return True
    return decision.cls not in (ToolClass.READ, ToolClass.NETWORK)


__all__ = [
    "REPLAY",
    "RUN",
    "UNCERTAIN",
    "UNCERTAIN_REPLY",
    "Decision",
    "TurnKernel",
    "side_effecting",
    "tools_digest",
]
