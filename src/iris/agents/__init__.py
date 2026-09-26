"""agents — the multi-agent layer: roles, handoffs and the orchestrator policy.

The lead is the main agent (it orchestrates, executes and authors). Two declared
specialists report to it: a read-only **researcher** and a heterogeneous
**critic**. Code owns the shape — which roles exist, what each may touch, how
many handoffs a turn gets, and how long it may take; JEV supplies the two
judgments (is this multi-part? is this draft grounded?) and fails open.

Multi-agent stays opt-in per turn: the lead decides to delegate by calling a
tool, and a budget breach degrades the answer rather than failing the turn.
"""

from __future__ import annotations

from iris.agents.handoff import (
    Claim,
    Handoff,
    Source,
    Spend,
    render_findings,
)
from iris.agents.roles import (
    CRITIC,
    READ_ONLY_TOOLS,
    RESEARCHER,
    ROLES,
    Role,
    RoleError,
    get_role,
    narrow,
    opposite_tier,
    validate_roles,
)

__all__ = [
    "CRITIC",
    "READ_ONLY_TOOLS",
    "RESEARCHER",
    "ROLES",
    "Claim",
    "Handoff",
    "Role",
    "RoleError",
    "Source",
    "Spend",
    "get_role",
    "narrow",
    "opposite_tier",
    "render_findings",
    "validate_roles",
]
