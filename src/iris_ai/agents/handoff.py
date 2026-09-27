"""Handoffs — the typed artifact every delegation produces.

A handoff is the *only* thing that crosses between the lead and a specialist.
That makes it the right place to enforce the two invariants this phase is built
on:

**Provenance.** A `Claim` carries its `sources` or is unsourced. There is no
third state where a finding is plausible but untraceable: `Claim.unsourced` is
derived from the sources, so it cannot disagree with them.

**Data, not instructions.** Specialists read untrusted material (ingested pages,
other people's files). `render_findings` quotes what comes back into the lead's
context, and `_sanitize` makes a finding structurally unable to escape the line
it is on: whitespace collapses, a leading `#` cannot forge a section header, and
a backtick cannot open a code fence. The finding's *text* survives; its ability
to impersonate the prompt does not.

Nothing here calls a model, reads a file or touches the network — a handoff is a
value, which is what makes the protocol cheap to test and impossible to misuse.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

KINDS: tuple[str, ...] = ("request", "report", "critique")
VERDICTS: tuple[str, ...] = ("supported", "unsupported", "partial")

# Stable refusal reasons. The orchestrator branches on these, so they are part
# of the interface, not prose.
REFUSED_DISABLED = "multi_agent_disabled"
REFUSED_BUDGET = "budget_exhausted"
REFUSED_DEADLINE = "deadline_exceeded"
REFUSED_NO_ROLE = "role_unavailable"

_EMPTY_REPORT = "## Findings from {role}\nno findings — the researcher found nothing to report."


@dataclass(frozen=True, slots=True)
class Source:
    """Where a claim came from. A path, plus the chunk when the record has one."""

    path: str
    chunk_index: int | None = None

    def label(self) -> str:
        if self.chunk_index is None:
            return self.path
        return f"{self.path}#{self.chunk_index}"

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "chunk_index": self.chunk_index}


@dataclass(frozen=True, slots=True)
class Claim:
    """One finding. Unsourced unless it names where it came from."""

    text: str
    sources: tuple[Source, ...] = ()

    @property
    def unsourced(self) -> bool:
        return not self.sources

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "unsourced": self.unsourced,
            "sources": [s.as_dict() for s in self.sources],
        }


@dataclass(frozen=True, slots=True)
class Spend:
    """What one handoff cost, so the multi-agent path is priced per turn."""

    tool_rounds: int = 0
    ms: int = 0
    tokens: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"tool_rounds": self.tool_rounds, "ms": self.ms, "tokens": self.tokens}


@dataclass(frozen=True, slots=True)
class Handoff:
    """One delegation, request or result, between the lead and a specialist."""

    id: str
    from_role: str
    to_role: str
    kind: str = "report"
    question: str = ""
    claims: tuple[Claim, ...] = ()
    verdict: str = ""
    score: float | None = None
    gate: float | None = None
    spend: Spend = field(default_factory=Spend)
    refused: str = ""
    truncated: bool = False

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown handoff kind {self.kind!r} — expected one of {', '.join(KINDS)}")
        if self.verdict and self.verdict not in VERDICTS:
            raise ValueError(
                f"unknown verdict {self.verdict!r} — expected one of {', '.join(VERDICTS)}"
            )

    @property
    def sourced(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if not c.unsourced)

    @property
    def unsourced(self) -> tuple[Claim, ...]:
        return tuple(c for c in self.claims if c.unsourced)

    def trace_summary(self) -> dict[str, Any]:
        """The turn-log payload: **scalars only**.

        `TurnLog.add` truncates top-level strings but passes nested structures
        through untouched, so a trace entry carrying `claims` would put a whole
        report into every trace line. What the trace needs is that a handoff
        happened, who for, and what it cost — the text stays in the turn.
        """
        out: dict[str, Any] = {
            "id": self.id,
            "from": self.from_role,
            "to": self.to_role,
            # NOT `kind`: `turnlog.record(kind, **fields)` already owns that
            # name (the event type, "handoff"), and a colliding key raises at
            # the call site. Found by the runner's first test run.
            "handoff_kind": self.kind,
            "claims": len(self.claims),
            "sourced": len(self.sourced),
            "unsourced": len(self.unsourced),
            "tool_rounds": self.spend.tool_rounds,
            "ms": self.spend.ms,
            "tokens": self.spend.tokens,
        }
        if self.verdict:
            out["verdict"] = self.verdict
        if self.score is not None:
            out["score"] = round(self.score, 3)
        if self.gate is not None:
            out["gate"] = round(self.gate, 3)
        if self.refused:
            out["refused"] = self.refused
        if self.truncated:
            out["truncated"] = True
        return out

    def as_dict(self) -> dict[str, Any]:
        """The full payload, claims included. For API responses, not traces."""
        out: dict[str, Any] = {
            "id": self.id,
            "from": self.from_role,
            "to": self.to_role,
            "kind": self.kind,
            "claims": [c.as_dict() for c in self.claims],
            "sourced": len(self.sourced),
            "unsourced": len(self.unsourced),
            "spend": self.spend.as_dict(),
        }
        if self.question:
            out["question"] = self.question
        if self.verdict:
            out["verdict"] = self.verdict
        if self.score is not None:
            out["score"] = round(self.score, 3)
        if self.gate is not None:
            out["gate"] = round(self.gate, 3)
        if self.refused:
            out["refused"] = self.refused
        if self.truncated:
            out["truncated"] = True
        return out


def _sanitize(text: str) -> str:
    """Flatten a finding onto one line that cannot impersonate the prompt.

    `split()` collapses every run of whitespace (newlines included) to single
    spaces, so a multi-line payload cannot break out of its bullet; the leading
    `#`/`>` strip stops it forging a section header; backticks are neutralized so
    it cannot open a fence and make the rest of the prompt look like code.
    """
    flat = " ".join(text.split())
    flat = flat.lstrip("#>-* ")
    return flat.replace("`", "'")


def render_findings(handoff: Handoff, *, max_chars: int) -> tuple[str, bool]:
    """The lead-facing rendering of one report.

    Returns `(text, truncated)`. Never raises: a handoff with no claims renders
    as an explicit "no findings", because silence and "I found nothing" must not
    look the same to the model.
    """
    if not handoff.claims:
        return _EMPTY_REPORT.format(role=handoff.from_role or "researcher"), False

    header = (
        f"## Findings from {handoff.from_role} "
        f"({len(handoff.sourced)} sourced, {len(handoff.unsourced)} unsourced)"
    )
    lines = [header]
    for claim in handoff.claims:
        text = _sanitize(claim.text)
        if claim.unsourced:
            lines.append(f"- [UNSOURCED — not in the record] {text}")
        else:
            where = ", ".join(s.label() for s in claim.sources)
            lines.append(f"- {text} [source: {where}]")

    body = "\n".join(lines)
    if len(body) <= max_chars:
        return body, False
    # Cut on a line boundary where we can, so a truncated finding never ends
    # mid-sentence looking complete.
    clipped = body[:max_chars]
    if "\n" in clipped:
        clipped = clipped[: clipped.rfind("\n")]
    return clipped, True
