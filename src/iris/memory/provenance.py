"""Provenance — the trust model for every piece of Iris's memory.

Every indexed chunk carries an origin class. This is a *security property*,
not a score: content from untrusted origins can never be promoted into the
curated core (MEMORY.md/USER.md), no matter how relevant or frequently
recalled it becomes.

Origins:
- owner:     written by the human (explicit "remember this", onboarding)
- agent:     derived by Iris's own pipelines (extraction, dreaming)
- untrusted: external content (imports, web pages, forwarded files)
- system:    operational logs (never promoted, never injected)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class Origin(str, Enum):
    OWNER = "owner"
    AGENT = "agent"
    UNTRUSTED = "untrusted"
    SYSTEM = "system"

    @property
    def promotable(self) -> bool:
        """Only owner/agent-derived content may graduate into curated core."""
        return self in (Origin.OWNER, Origin.AGENT)


@dataclass(slots=True)
class Provenance:
    origin: Origin
    observed_at: datetime = field(default_factory=datetime.now)
    source: str = ""          # file path or session id that produced this
    session_id: str = ""      # conversation thread, when applicable
    supersedes: str = ""      # supersession key of the entry this retires

    @property
    def observed_date(self) -> date:
        return self.observed_at.date()