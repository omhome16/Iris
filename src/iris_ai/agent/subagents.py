"""Subagents — the researcher role, kept at its shipped import path.

This module used to hold the hardcoded research subgraph. In P5 that graph became
`iris_ai.agents.runner.RoleRunner`, parameterized by a declared `Role`, so the
worker is no longer a special case: it is the researcher role.

`ResearchSubagent` survives as a **name for that configuration**, not a second
implementation — it wraps `RoleRunner(runtime, RESEARCHER)` and keeps the
pre-P5 `research() -> str` API, so `engine.py`, the `deep_dive` tool and the
existing tests are unchanged.

Why the researcher exists at all: deep questions (temporal / multi-hop / "dig
through everything") are the case where spending cheap-tier tokens beats
spending strong-tier ones. It runs read-only on the escalation lane, capped at
three tool rounds, and reports back as context the lead acts on.
"""

from __future__ import annotations

from iris_ai.agent.runtime import Runtime
from iris_ai.agents.roles import RESEARCHER
from iris_ai.agents.runner import RoleRunner


class ResearchSubagent(RoleRunner):
    """The researcher role with the shipped `research()` signature."""

    def __init__(self, runtime: Runtime) -> None:
        super().__init__(runtime, RESEARCHER)

    async def research(self, query: str, *, session_id: str = "") -> str:
        """Run the researcher and return its report text ("" if it found nothing)."""
        return await self.report(query, session_id=session_id)
