"""Approval integrity — an approval grants the action it showed, exactly once.

The audit's G4 finding: the interrupt carried the arguments, but nothing *pinned*
them. Three failure modes follow from that, and each one is cheap to close:

1. **An unbound approval.** If a resume can target different arguments than the
   ones the owner saw, "approve" stops meaning what it says. The envelope carries
   the **effective digest of the arguments after edits**, so the approval is bound
   to a specific action rather than to a slot in a conversation.
2. **A replayed approval.** One `tool_call_id` may be granted once per thread. A
   double-submitted resume is otherwise indistinguishable from a deliberate second
   grant.
3. **A resume of a finished run.** Resuming a thread with no interrupt waiting is
   not an approval — it is a bug or an attack, and it should be refused with a
   reason rather than handed to the graph.

Plus the rule that makes the rest matter: **fail closed for side-effecting
tools**. An envelope for an action that changes the world and carries no digest
is not trustworthy, and is refused rather than granted by default.

This module is pure — no graph, no I/O — so the invariants are unit-testable on
their own and the graph wiring stays thin enough to review.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field


def effective_digest(args: object) -> str:
    """A stable digest of the arguments as they will actually be executed.

    Sorted keys and `default=str`, so the digest is a function of the *meaning* of
    the call and not of key order or an unserialisable value.
    """
    try:
        canonical = json.dumps(args if args is not None else {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True, slots=True)
class Envelope:
    """What the owner is being asked to approve, pinned to a specific action."""

    action: str
    call_id: str = ""
    args: object = None
    side_effecting: bool = True

    @property
    def digest(self) -> str:
        return effective_digest(self.args)

    def payload(self) -> dict:
        """The interrupt payload. `digest` and `call_id` are what make it bindable."""
        return {
            "type": "approval",
            "action": self.action,
            "call_id": self.call_id,
            "digest": self.digest,
            "side_effecting": self.side_effecting,
        }


@dataclass(frozen=True, slots=True)
class ResumeVerdict:
    allowed: bool
    reason: str = ""

    @property
    def refused(self) -> bool:
        return not self.allowed


class ReplayGuard:
    """One `tool_call_id` grants once per thread."""

    def __init__(self) -> None:
        self._granted: set[tuple[str, str]] = set()

    def granted(self, thread: str, call_id: str) -> bool:
        return bool(call_id) and (thread, call_id) in self._granted

    def grant(self, thread: str, call_id: str) -> None:
        if call_id:
            self._granted.add((thread, call_id))

    def clear(self, thread: str) -> None:
        self._granted = {key for key in self._granted if key[0] != thread}


@dataclass
class ApprovalPolicy:
    """The two switches, so a deployment can loosen them deliberately."""

    bind_digest: bool = True
    guard_replay: bool = True


@dataclass
class ApprovalGate:
    """The decision point. Holds the replay memory; everything else is pure."""

    policy: ApprovalPolicy = field(default_factory=ApprovalPolicy)
    replay: ReplayGuard = field(default_factory=ReplayGuard)

    def verify(
        self,
        *,
        pending: Mapping | None,
        decision: str,
        thread: str,
    ) -> ResumeVerdict:
        """Could this resume be honoured? `pending` is the waiting interrupt payload."""
        if not pending or pending.get("type") != "approval":
            return ResumeVerdict(
                False,
                "there is no approval waiting on this thread — nothing to resume",
            )

        call_id = str(pending.get("call_id") or "")
        if self.policy.guard_replay and self.replay.granted(thread, call_id):
            return ResumeVerdict(False, f"approval {call_id!r} was already granted on this thread")

        # Fail closed: a world-changing action with nothing to bind to is refused
        # whether the owner said yes or no.
        if pending.get("side_effecting") and self.policy.bind_digest and not pending.get("digest"):
            return ResumeVerdict(
                False,
                "this approval carries no argument digest, so it cannot be bound to what was shown",
            )

        if decision == "approved" and self.policy.guard_replay:
            self.replay.grant(thread, call_id)
        return ResumeVerdict(True)
