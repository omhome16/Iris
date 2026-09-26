"""Tool classes, default policy, and the visible surface."""

from __future__ import annotations

import pytest

from iris_ai.agent.tools import TOOL_NAMES
from iris_ai.toolpolicy import (
    CLASS_DEFAULTS,
    TOOL_DECLARATIONS,
    Policy,
    PolicyError,
    ToolClass,
    declaration,
    parse_overrides,
    policy_snapshot,
    resolve,
    surface_order,
    unknown_overrides,
)

# ── the declaration is total, in both directions ────────────────────────────


def test_every_shipped_tool_is_declared():
    undeclared = TOOL_NAMES - set(TOOL_DECLARATIONS)
    assert not undeclared, f"tools with no declared class: {sorted(undeclared)}"


def test_no_declaration_for_a_tool_that_does_not_exist():
    """The other direction. A declared tool that no longer exists is a stale
    allowlist entry — the exact failure mode this module replaces."""
    phantom = set(TOOL_DECLARATIONS) - TOOL_NAMES
    assert not phantom, f"declared tools that do not exist: {sorted(phantom)}"


def test_declarations_are_well_formed():
    for name, decl in TOOL_DECLARATIONS.items():
        assert isinstance(decl.cls, ToolClass), name
        assert decl.surface in ("core", "extended"), name


def test_every_class_has_a_default_policy():
    assert set(CLASS_DEFAULTS) == set(ToolClass)


def test_undeclared_tool_raises():
    with pytest.raises(PolicyError, match="no declared class"):
        declaration("not_a_tool")


def test_no_control_tool_is_ever_core():
    """A tool that drives a screen is never in the always-visible set: `core`
    means 'must not be hidden', and control must be able to be."""
    offenders = [n for n, d in TOOL_DECLARATIONS.items() if d.cls is ToolClass.CONTROL and d.surface == "core"]
    assert not offenders, offenders


# ── defaults preserve shipped behaviour ─────────────────────────────────────


def test_control_and_credentialed_default_to_ask():
    assert CLASS_DEFAULTS[ToolClass.CONTROL] is Policy.ASK
    assert CLASS_DEFAULTS[ToolClass.CREDENTIALED] is Policy.ASK


def test_shipped_tools_are_unaffected_by_the_new_layer():
    """The point of getting the defaults right: nothing the owner already relies
    on changes behaviour. Every non-control, non-credentialed tool resolves to
    allow with no overrides configured."""
    for name, decl in TOOL_DECLARATIONS.items():
        if decl.cls in (ToolClass.CONTROL, ToolClass.CREDENTIALED):
            continue
        assert resolve(name).policy is Policy.ALLOW, name


def test_class_default_source_is_named():
    decision = resolve("memory_search")
    assert decision.source == "class-default"
    assert decision.allowed and not decision.needs_approval and not decision.denied


# ── resolution: most specific wins ─────────────────────────────────────────


def test_tool_override_beats_class_override():
    decision = resolve("file_read", {"read": "ask", "file_read": "allow"})
    assert decision.policy is Policy.ALLOW
    assert decision.source == "override:tool"


def test_class_override_beats_class_default():
    decision = resolve("memory_search", {"read": "ask"})
    assert decision.policy is Policy.ASK
    assert decision.source == "override:class"


def test_overrides_accept_the_env_string_form():
    decision = resolve("send_message", "send_message=deny")
    assert decision.policy is Policy.DENY
    assert decision.source == "override:tool"


def test_overrides_accept_parsed_policy_values():
    decision = resolve("computer", {"control": Policy.ALLOW})
    assert decision.policy is Policy.ALLOW


# ── deny always wins ───────────────────────────────────────────────────────


def test_class_deny_cannot_be_reopened_per_tool():
    """The invariant: a class-wide deny is a decision about the class, so one
    tool cannot be carved out of it — that is how an allowlist erodes."""
    decision = resolve("file_write", {"filesystem": "deny", "file_write": "allow"})
    assert decision.policy is Policy.DENY
    assert decision.source == "override:class"
    assert "denied" in decision.reason


def test_tool_deny_beats_a_class_allow():
    decision = resolve("web_search", {"network": "allow", "web_search": "deny"})
    assert decision.policy is Policy.DENY
    assert decision.source == "override:tool"


# ── overrides fail loudly, and report typos softly ─────────────────────────


@pytest.mark.parametrize("bad", ["send_message", "send_message=maybe", "=deny", "control=block"])
def test_malformed_override_raises(bad):
    with pytest.raises(PolicyError):
        parse_overrides(bad)


def test_empty_overrides_are_fine():
    assert parse_overrides("") == {}
    assert parse_overrides(None) == {}
    assert parse_overrides("  ") == {}


def test_unknown_override_keys_are_reported_not_raised():
    """A typo in `.env` must not stop Iris booting — but it must not be silent."""
    assert unknown_overrides("send_messge=deny,control=ask") == ["send_messge"]
    assert unknown_overrides("control=ask") == []


# ── the readout ────────────────────────────────────────────────────────────


def test_policy_snapshot_covers_every_tool_and_names_its_source():
    rows = policy_snapshot({"computer": "deny"})
    assert {r["tool"] for r in rows} == set(TOOL_DECLARATIONS)
    computer = next(r for r in rows if r["tool"] == "computer")
    assert computer == {
        "tool": "computer",
        "class": "control",
        "policy": "deny",
        "source": "override:tool",
        "surface": "extended",
    }


# ── the surface ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("budget", [0, 1, 5, 19, 20, 23, 50, 1000])
def test_core_tools_are_never_deferred(budget):
    visible, _deferred = surface_order(budget)
    core = [n for n, d in TOOL_DECLARATIONS.items() if d.surface == "core"]
    missing = set(core) - set(visible)
    assert not missing, f"budget {budget} hid {sorted(missing)}"


@pytest.mark.parametrize("budget", [0, 1, 5, 19, 20, 23, 50])
def test_visible_and_deferred_partition_every_tool(budget):
    visible, deferred = surface_order(budget)
    assert set(visible) | set(deferred) == set(TOOL_DECLARATIONS)
    assert not set(visible) & set(deferred)
    assert len(visible) == len(set(visible))


def test_budget_of_zero_means_no_budget_not_blindness():
    visible, deferred = surface_order(0)
    assert not deferred
    assert set(visible) == set(TOOL_DECLARATIONS)


def test_budget_is_respected_above_the_core_count():
    visible, _ = surface_order(18)
    assert len(visible) == 18


def test_deferral_takes_the_last_declared_extended_tool_first():
    """Declaration order *is* the priority order, so deferral is deterministic
    and reviewable in the diff rather than an emergent property."""
    core = [n for n, d in TOOL_DECLARATIONS.items() if d.surface == "core"]
    extended = [n for n, d in TOOL_DECLARATIONS.items() if d.surface == "extended"]
    budget = len(core) + 2
    visible, deferred = surface_order(budget)
    assert len(visible) == budget
    assert deferred == extended[2:]
    assert extended[0] in visible


def test_promotion_wins_over_the_budget():
    """A connected channel's tools are not optional, so a promotion cannot be
    deferred away to save schema bytes."""
    visible, deferred = surface_order(17, promoted=["send_message"])
    assert "send_message" in visible
    assert "send_message" not in deferred


def test_promotion_can_push_the_surface_past_the_budget():
    """Promotion beats the budget, so it wins *even when it overflows*. The
    alternative — defrauding the budget by dropping some other tool — would make
    a connected channel's ability to deliver depend on a token count."""
    core_count = sum(1 for d in TOOL_DECLARATIONS.values() if d.surface == "core")
    visible, deferred = surface_order(core_count, promoted=["send_message"])
    assert len(visible) == core_count + 1
    assert len(visible) > core_count  # past the budget
    assert "send_message" in visible
    assert deferred
    assert "send_message" not in deferred


def test_budget_can_be_never_binding():
    visible, deferred = surface_order(100)
    assert not deferred
    assert set(visible) == set(TOOL_DECLARATIONS)
