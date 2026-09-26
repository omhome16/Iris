"""Approval integrity wired through the graph (audit G4).

The invariants are unit-tested in `tests/test_approval.py`; these tests prove the
wiring: the payload the owner sees is *bound*, a replayed approval is refused, and
a resume with nothing waiting is refused rather than handed to the graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver

from iris.agent.chat import ApprovalRequired, ChatGraph
from test_agent_graph import ForgetLLM, _forget_runtime


def _digest_shaped(value: object) -> bool:
    return isinstance(value, str) and len(value) == 16 and all(c in "0123456789abcdef" for c in value)


async def test_the_forget_approval_is_bound_to_a_digest_and_a_call(tmp_path: Path):
    _files, runtime = await _forget_runtime(tmp_path, ForgetLLM())
    graph = ChatGraph(runtime, MemorySaver())
    with pytest.raises(ApprovalRequired) as exc:
        await graph.respond("forget about my lease", session_id="t-bind")
    payload = exc.value.payload
    assert payload["type"] == "approval"
    assert payload["action"] == "forget"
    assert payload["side_effecting"] is True
    assert _digest_shaped(payload["digest"])
    assert payload["call_id"]  # bound to a specific tool call
    assert "query" in payload and "query" not in payload["digest"]  # never the args themselves


async def test_a_replayed_approval_is_refused_and_the_action_does_not_happen(tmp_path: Path):
    files, runtime = await _forget_runtime(tmp_path, ForgetLLM())
    graph = ChatGraph(runtime, MemorySaver())
    with pytest.raises(ApprovalRequired) as exc:
        await graph.respond("forget about my lease", session_id="t-replay")

    # Pretend the same call_id was already granted (a double-submitted resume).
    graph.approvals.replay.grant("t-replay", exc.value.payload["call_id"])

    reply = await graph.resume("t-replay", decision="approved")
    assert "already granted" in reply
    assert "superseded" not in files.read(files.memory)  # the write did not run


async def test_resuming_a_thread_with_nothing_waiting_is_refused(tmp_path: Path):
    _files, runtime = await _forget_runtime(tmp_path, ForgetLLM())
    graph = ChatGraph(runtime, MemorySaver())
    reply = await graph.resume("never-interrupted", decision="approved")
    assert "no approval waiting" in reply


async def test_an_unbound_side_effecting_approval_is_refused(tmp_path: Path):
    """Fail closed: if the envelope carries no digest, no grant is given."""
    _files, runtime = await _forget_runtime(tmp_path, ForgetLLM())
    graph = ChatGraph(runtime, MemorySaver())
    verdict = graph.approvals.verify(
        pending={"type": "approval", "action": "forget", "side_effecting": True},
        decision="approved",
        thread="t-unbound",
    )
    assert verdict.refused
    assert "no argument digest" in verdict.reason


async def test_a_bound_forget_still_works_end_to_end(tmp_path: Path):
    files, runtime = await _forget_runtime(tmp_path, ForgetLLM())
    graph = ChatGraph(runtime, MemorySaver())
    with pytest.raises(ApprovalRequired):
        await graph.respond("forget about my lease", session_id="t-ok")
    await graph.resume("t-ok", decision="approved")
    assert "superseded" in files.read(files.memory)
