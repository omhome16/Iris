"""Approval integrity: digest binding, replay guard, terminal-state guard (G4)."""

from __future__ import annotations

from iris.approval import (
    ApprovalGate,
    ApprovalPolicy,
    Envelope,
    ReplayGuard,
    effective_digest,
)


def test_the_digest_is_a_function_of_meaning_not_key_order():
    assert effective_digest({"a": 1, "b": 2}) == effective_digest({"b": 2, "a": 1})
    assert effective_digest({"a": 1}) != effective_digest({"a": 2})


def test_the_digest_never_raises_on_an_unserialisable_value():
    assert effective_digest(object())
    assert effective_digest(None) == effective_digest({})


def test_the_envelope_pins_the_action_to_a_digest_and_a_call():
    payload = Envelope(action="forget", call_id="call_7", args={"query": "lease"}).payload()
    assert payload["type"] == "approval"
    assert payload["action"] == "forget"
    assert payload["call_id"] == "call_7"
    assert payload["digest"] == effective_digest({"query": "lease"})
    assert payload["side_effecting"] is True


# ── terminal-state guard ────────────────────────────────────────────────────


def test_resuming_a_thread_with_nothing_waiting_is_refused():
    verdict = ApprovalGate().verify(pending=None, decision="approved", thread="t1")
    assert verdict.refused
    assert "nothing to resume" in verdict.reason


def test_a_pending_approval_can_be_granted():
    gate = ApprovalGate()
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}).payload()
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed


# ── replay guard ────────────────────────────────────────────────────────────


def test_one_call_id_grants_once_per_thread():
    gate = ApprovalGate()
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}).payload()
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed
    replay = gate.verify(pending=pending, decision="approved", thread="t1")
    assert replay.refused
    assert "already granted" in replay.reason


def test_the_same_call_id_on_another_thread_is_not_a_replay():
    gate = ApprovalGate()
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}).payload()
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed
    assert gate.verify(pending=pending, decision="approved", thread="t2").allowed


def test_a_cancelled_decision_does_not_consume_the_grant():
    """Saying no must not burn the call: the owner can be asked again."""
    gate = ApprovalGate()
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}).payload()
    assert gate.verify(pending=pending, decision="cancelled", thread="t1").allowed
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed


def test_replay_guard_can_be_switched_off():
    gate = ApprovalGate(policy=ApprovalPolicy(guard_replay=False))
    pending = Envelope(action="forget", call_id="c1", args={"query": "x"}).payload()
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed


# ── fail closed for side-effecting actions ─────────────────────────────────


def test_a_side_effecting_approval_without_a_digest_is_refused():
    gate = ApprovalGate()
    pending = {"type": "approval", "action": "forget", "side_effecting": True}
    verdict = gate.verify(pending=pending, decision="approved", thread="t1")
    assert verdict.refused
    assert "no argument digest" in verdict.reason


def test_a_read_only_approval_without_a_digest_is_fine():
    gate = ApprovalGate()
    pending = {"type": "approval", "action": "peek", "side_effecting": False}
    assert gate.verify(pending=pending, decision="approved", thread="t1").allowed


def test_a_non_approval_payload_is_not_resumable():
    gate = ApprovalGate()
    assert gate.verify(pending={"type": "question"}, decision="approved", thread="t1").refused


def test_the_replay_guard_is_per_thread():
    guard = ReplayGuard()
    guard.grant("t1", "c1")
    assert guard.granted("t1", "c1")
    assert not guard.granted("t2", "c1")
    guard.clear("t1")
    assert not guard.granted("t1", "c1")
