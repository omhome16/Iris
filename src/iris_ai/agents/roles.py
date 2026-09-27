"""Roles — who may be delegated to, and what each one is allowed to touch.

P5's multi-agent layer is deliberately small. The **lead** is the main agent
itself: it orchestrates, it executes, and it authors the answer. Two declared
specialists report to it:

- **researcher** — the generalized worker that already shipped behind the
  `deep_dive` tool. Cheap tier, read-only, bounded rounds.
- **critic** — new. Checks a draft against the findings and the record, and
  answers per claim with a source.

A role is **data, not a class hierarchy**: its tool surface, tier, round cap and
output cap are declared here and enforced by the runner and the orchestrator.
Three rules make the shape safe by construction:

1. **A role may only narrow.** `narrow()` intersects the role's allowlist with
   what the session actually grants, so a role can never reach a tool the lead
   was denied. The same rule P4 applies to a skill's `allowed-tools`.
2. **A role may not name a tool nobody declared.** `validate_roles` checks
   against the same `TOOL_NAMES` set a skill manifest is checked against, so a
   typo fails at startup instead of reaching `dispatch` as a hallucination.
3. **A specialist cannot delegate.** `deep_dive` is absent from every role's
   toolset: a subagent that can spawn a subagent is a recursion with no bottom.

Roles deliberately *not* declared here — an executor (the lead already executes,
with the full tool surface and the P4 skill policy around it), a memory curator
(`capture`, `dreaming`, `reflection` and `skill_write` already do that), a
planner (the lead's ReAct loop is the planner), and a citation agent (promoted to
a protocol rule: every claim carries provenance or is marked unsourced). See the
P5 spec for the reasoning and the industry sources behind it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

# Capabilities that only read. Used to state the "no specialist may mutate or
# deliver" rule in one place, and asserted against the real tool surface so the
# set cannot drift into naming tools that do not exist.
#
# `ingest_url` and `web_search` are deliberately absent: ingest *writes* memory,
# and a specialist with network fetch is a much larger blast radius than the
# read-only worker this phase needs.
READ_ONLY_TOOLS: frozenset[str] = frozenset(
    {
        "memory_search",
        "file_read",
        "file_list",
        "get_chat_history",
    }
)

_TIERS: tuple[str, ...] = ("cheap", "strong")
_LANES: tuple[str, ...] = ("default", "escalate")


class RoleError(RuntimeError):
    """A role declaration is unusable — raised at startup, never mid-turn."""


@dataclass(frozen=True, slots=True)
class Role:
    """One declared specialist.

    `tools` names *capabilities* from the registered tool surface. The runner
    binds the implementations (the researcher's `memory_search`, for instance, is
    fixed to the escalation lane), so the allowlist states what a role is for,
    not the exact schema it gets.
    """

    name: str
    description: str
    tier: str
    system_prompt: str
    tools: frozenset[str]
    max_tool_rounds: int = 3
    max_output_chars: int = 4000
    # Which recall lane the role's `memory_search` is bound to: the researcher
    # digs through old daily notes (escalate), the critic checks the current
    # record (default). Declared here so the runner binds implementations
    # instead of switching on the role's name.
    search_lane: str = "escalate"


# Moved verbatim from `agent/subagents.py` when the worker became a role: the
# prompt is byte-identical, so P5 changes no behaviour of the shipped worker.
_RESEARCHER_PROMPT = """You are Iris's research subagent. Dig through long-term
memory (memory_search, lane='escalate' for daily notes) and sandbox files
(file_read) to answer the owner's question. Be thorough but concise; your
report is injected into Iris's context. If memory has nothing, say so plainly.
Never fabricate facts. Finish with a short report."""

_CRITIC_PROMPT = """You are Iris's critic. You are given a draft answer and the
findings it was built from. Your job is to check facts, not to rewrite prose.

For every factual claim in the draft, decide whether the findings or memory
actually support it. Judge each claim separately and name the source you checked
(path, and the chunk if you have it). Follow these rules exactly:

- A claim you cannot trace to a source is **unsupported**. Say so, and say which
  source you looked in. Never invent a source, never assume a claim is true
  because it sounds plausible.
- If a claim contradicts what you find, quote the contradicting line.
- Do not propose a full rewrite, and do not add facts of your own. Your verdict
  is what the lead acts on.

Finish with one line per claim: `supported | unsupported | partial — <claim>
— <source>`. If there were no findings to check against, say that instead."""


RESEARCHER = Role(
    name="researcher",
    description=(
        "Digs through long-term memory, daily notes and sandbox files for "
        "temporal, multi-hop or 'dig through everything' questions. Read-only; "
        "returns findings with their sources."
    ),
    tier="cheap",
    system_prompt=_RESEARCHER_PROMPT,
    tools=frozenset({"memory_search", "file_read"}),
    max_tool_rounds=3,
)

CRITIC = Role(
    name="critic",
    description=(
        "Checks a draft answer claim by claim against the findings and the "
        "record, and reports which claims are unsupported. Read-only; never "
        "rewrites the answer and never invents a source."
    ),
    # The opposite tier from the researcher on purpose. Self-correction on the
    # producer's own model mostly agrees with the producer (self-preference
    # bias), so the critic is declared heterogeneous; the orchestrator flips the
    # tier again when the producer is the strong-tier lead.
    tier="strong",
    system_prompt=_CRITIC_PROMPT,
    tools=frozenset({"memory_search", "file_read"}),
    max_tool_rounds=2,
    # The critic checks the *current* record, which is the default lane; what
    # the researcher dug out of old daily notes arrives with the draft.
    search_lane="default",
)

ROLES: Mapping[str, Role] = {RESEARCHER.name: RESEARCHER, CRITIC.name: CRITIC}


def get_role(name: str) -> Role:
    """Look a role up by name, naming the alternatives when one is misspelled."""
    role = ROLES.get(name)
    if role is None:
        known = ", ".join(sorted(ROLES))
        raise RoleError(f"unknown role {name!r} — declared roles are: {known}")
    return role


def validate_roles(roles: Iterable[Role], *, known_tools: frozenset[str] | set[str]) -> None:
    """Reject a role that could not be honoured at call time.

    Called at startup with the real `TOOL_NAMES`, mirroring how a skill manifest
    is validated — a name nobody declared is a hallucination waiting to happen.
    """
    seen: set[str] = set()
    for role in roles:
        if not role.name:
            raise RoleError("a role needs a name")
        if role.name in seen:
            raise RoleError(f"duplicate role {role.name!r}")
        seen.add(role.name)
        if role.tier not in _TIERS:
            raise RoleError(
                f"role {role.name!r} has tier {role.tier!r} — expected one of {', '.join(_TIERS)}"
            )
        if not role.system_prompt.strip():
            raise RoleError(f"role {role.name!r} has an empty system prompt")
        unknown = set(role.tools) - set(known_tools)
        if unknown:
            raise RoleError(
                f"role {role.name!r} names tool(s) not in the tool surface: {sorted(unknown)}"
            )
        if role.search_lane not in _LANES:
            raise RoleError(
                f"role {role.name!r} has search lane {role.search_lane!r} — "
                f"expected one of {', '.join(_LANES)}"
            )
        if role.max_tool_rounds < 1:
            raise RoleError(f"role {role.name!r} needs at least one tool round")
        if role.max_output_chars < 1:
            raise RoleError(f"role {role.name!r} needs a positive output cap")


def narrow(role: Role, granted: Sequence[str] | set[str] | None) -> set[str]:
    """The tools a role may actually use this turn.

    `granted` is what the session already allows (`None` = unrestricted, as for
    an owner turn). Either way the result is bounded by the role: a role never
    widens a session, and a session never widens a role.
    """
    if granted is None:
        return set(role.tools)
    return set(role.tools) & set(granted)


def opposite_tier(tier: str) -> str:
    """The other model tier — the heterogeneity the critic depends on.

    Different tiers mean different candidate models (`settings.llm_candidates`),
    which is what breaks the correlation between a producer and its reviewer.
    """
    return "strong" if tier == "cheap" else "cheap"
