"""P5 — roles are declared data, and a role can only ever narrow.

The point of this suite is not that two roles exist; it is that the *shape* of a
role cannot be widened by accident. A role that could name a tool nobody
declared, or reach the delegating tool itself, would let a subagent grant itself
capability — the exact thing P4's skill policy refuses to allow.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

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


def test_the_pack_is_exactly_researcher_and_critic():
    """Two specialists, deliberately. The lead is the main agent, not a Role."""
    assert set(ROLES) == {"researcher", "critic"}
    assert ROLES["researcher"] is RESEARCHER
    assert ROLES["critic"] is CRITIC


def test_roles_are_immutable():
    with pytest.raises(FrozenInstanceError):
        RESEARCHER.tier = "strong"  # type: ignore[misc]


def test_researcher_keeps_the_shipped_bounds():
    """Today's subagent is cheap-tier, capped at 3 tool rounds. P5 must not
    silently loosen the worker that already exists."""
    assert RESEARCHER.tier == "cheap"
    assert RESEARCHER.max_tool_rounds == 3
    assert RESEARCHER.tools <= READ_ONLY_TOOLS


def test_critic_is_read_only_too():
    assert CRITIC.tools <= READ_ONLY_TOOLS


def test_no_role_may_delegate():
    """A specialist that can call `deep_dive` is a recursion with no bottom."""
    for role in ROLES.values():
        assert "deep_dive" not in role.tools


@pytest.mark.parametrize("role", [RESEARCHER, CRITIC])
def test_no_role_may_mutate_or_deliver(role):
    """Writes, scheduling, memory curation and Telegram delivery stay with the
    lead. A specialist returns findings; it does not act on the world."""
    forbidden = {
        "file_create",
        "file_write",
        "remember",
        "note",
        "forget",
        "dream_now",
        "skill_write",
        "skill_revise",
        "skill_run",
        "schedule_task",
        "send_message",
        "send_photo",
        "ingest_url",
    }
    assert role.tools & forbidden == set()


@pytest.mark.parametrize("role", [RESEARCHER, CRITIC])
def test_every_role_can_be_delegated_to(role):
    """A role with no description cannot be described in a delegation, and a
    vague delegation is the documented cause of duplicated subagent work."""
    assert role.name
    assert role.description
    assert role.system_prompt


def test_read_only_tools_are_real_and_cannot_drift():
    from iris.agent.tools import TOOL_NAMES

    assert READ_ONLY_TOOLS <= TOOL_NAMES


def test_the_shipped_pack_validates_against_the_tool_surface():
    from iris.agent.tools import TOOL_NAMES

    validate_roles(ROLES.values(), known_tools=TOOL_NAMES)


def test_an_undeclared_tool_name_is_an_error():
    """Same rule P4 applies to a skill manifest: a role may not name a tool
    nobody declared, because that name would reach dispatch as a hallucination."""
    bogus = Role(
        name="bogus",
        description="d",
        tier="cheap",
        system_prompt="p",
        tools=frozenset({"totally_not_a_tool"}),
    )
    with pytest.raises(RoleError) as exc:
        validate_roles([bogus], known_tools=frozenset({"memory_search"}))
    assert "totally_not_a_tool" in str(exc.value)


def test_an_unknown_tier_is_an_error():
    bogus = Role(
        name="bogus",
        description="d",
        tier="turbo",
        system_prompt="p",
        tools=frozenset(),
    )
    with pytest.raises(RoleError) as exc:
        validate_roles([bogus], known_tools=frozenset())
    assert "turbo" in str(exc.value)


def test_get_role_names_the_known_roles_on_a_typo():
    assert get_role("researcher") is RESEARCHER
    with pytest.raises(RoleError) as exc:
        get_role("resercher")  # misspelled on purpose
    assert "researcher" in str(exc.value)


def test_narrow_intersects_and_never_widens():
    """The role's allowlist and what the session actually grants both apply."""
    granted = {"memory_search", "file_read", "file_write", "send_message"}
    assert narrow(RESEARCHER, granted) == {"memory_search", "file_read"}
    # A tool the role wants but the session does not grant is dropped.
    assert narrow(RESEARCHER, {"file_read"}) == {"file_read"}
    # Nothing granted means nothing runs.
    assert narrow(RESEARCHER, set()) == set()
    # An unrestricted session still cannot widen the role.
    assert narrow(RESEARCHER, None) == set(RESEARCHER.tools)
    assert narrow(RESEARCHER, None) <= READ_ONLY_TOOLS


def test_roles_declare_their_recall_lane():
    """The researcher digs old daily notes (escalate); the critic checks the
    current record (default). Declared, so the runner binds implementations
    instead of switching on the role's name."""
    assert RESEARCHER.search_lane == "escalate"
    assert CRITIC.search_lane == "default"


def test_an_unknown_lane_is_an_error():
    bogus = Role(
        name="bogus",
        description="d",
        tier="cheap",
        system_prompt="p",
        tools=frozenset(),
        search_lane="sideways",
    )
    with pytest.raises(RoleError) as exc:
        validate_roles([bogus], known_tools=frozenset())
    assert "sideways" in str(exc.value)


def test_the_critic_reviews_from_the_other_tier():
    """Heterogeneity is the mitigation for self-preference bias: an evaluator on
    the producer's own model mostly agrees with it. Code flips the tier."""
    assert opposite_tier("cheap") == "strong"
    assert opposite_tier("strong") == "cheap"
    assert CRITIC.tier == opposite_tier(RESEARCHER.tier)
