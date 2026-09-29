"""The journal and the tool boundary — exactly-once across a restart.

Phase 5's gate is three claims, and each gets a test that would fail if the
behaviour were removed:

1. **kill mid-tool** — a process that dies between `tool_start` and `tool_end`
   leaves a side-effecting action of *unknown* outcome, and the boundary refuses
   to repeat it while still letting a safe one through;
2. **replay** — a call that settled is answered from the journal, so re-entering
   the tools node (which is exactly what an approval resume does) cannot do a
   write twice;
3. **no replay divergence** — the same journal replayed twice yields the same
   replies, and the tools involved are not called again.

There is a fourth, quieter one: an *interrupted* step is not a crashed one. The
graph pauses for the owner's approval and then re-enters the same step; treating
that pause as an unknown outcome would refuse the very thing the owner approved.
"""

from __future__ import annotations

import re
from pathlib import Path

from iris_ai.approval import effective_digest
from iris_ai.kernel import TurnJournal, TurnKernel, side_effecting, tools_digest
from iris_ai.kernel.journal import DONE, INTERRUPTED, STARTED, TurnRecord


def _journal(tmp_path: Path) -> TurnJournal:
    return TurnJournal(tmp_path / "journal.jsonl", max_bytes=10_000_000)


# ── the journal ─────────────────────────────────────────────────────────────


def test_a_settled_step_is_read_back_with_its_reply(tmp_path: Path):
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="remember", digest="d", side_effecting=True)
    journal.tool_end("t1", "1", call_id="c1", ok=True, result='{"ok": true}', error="")

    (step,) = journal.turn("t1", "1").steps.values()
    assert step.status == DONE
    assert step.result == '{"ok": true}'
    assert step.settled is True


def test_a_started_step_with_no_end_is_the_crash_case(tmp_path: Path):
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="send_message", side_effecting=True)

    (step,) = journal.turn("t1", "1").steps.values()
    assert step.status == STARTED
    assert step.settled is False
    assert journal.unfinished() == [("t1", "1")]


def test_an_interrupt_is_not_a_crash(tmp_path: Path):
    """The distinction the whole boundary rests on: a pause is not an unknown."""
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="forget")
    journal.tool_interrupt("t1", "1", call_id="c1")

    (step,) = journal.turn("t1", "1").steps.values()
    assert step.status == INTERRUPTED
    assert step.waiting is True
    assert journal.unfinished() == [], "a paused step is not a process that died"


def test_a_spent_approval_survives_the_process(tmp_path: Path):
    """A restart must not forget a grant — otherwise a replay becomes a second grant."""
    journal = _journal(tmp_path)
    journal.approval_granted("t1", "1", call_id="c1", action="forget")
    assert journal.granted("t1", "c1") is True
    assert journal.granted("t1", "c-other") is False
    assert journal.granted("t2", "c1") is False  # per thread, like the guard


def test_a_corrupt_line_is_skipped_not_fatal(tmp_path: Path):
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="file_read")
    with journal.path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    journal.tool_end("t1", "1", call_id="c1", ok=True, result="{}")
    assert journal.turn("t1", "1").steps["c1"].settled is True


def test_turn_start_records_what_the_turn_ran_with(tmp_path: Path):
    journal = _journal(tmp_path)
    journal.turn_start("t1", "1", prompt_version="v7", tools="abc123")
    record: TurnRecord = journal.turn("t1", "1")
    assert record.prompt_version == "v7"
    assert record.tools_digest == "abc123"


def test_the_tools_digest_is_order_independent():
    assert tools_digest(["b", "a"]) == tools_digest(["a", "b"])
    assert tools_digest(["a"]) != tools_digest(["a", "b"])


# ── the boundary ────────────────────────────────────────────────────────────


def test_a_settled_call_replays_instead_of_running_again(tmp_path: Path):
    journal = _journal(tmp_path)
    kernel = TurnKernel(journal)
    kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="remember", args={"text": "x"})
    kernel.after_tool(thread="t1", turn="1", call_id="c1", ok=True, reply='{"ok": true, "remembered": 1}')

    again = kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="remember", args={"text": "x"})
    assert again.replay == '{"ok": true, "remembered": 1}'
    assert "already ran" in again.reason


def test_replaying_twice_yields_the_same_replies_and_runs_nothing(tmp_path: Path):
    """The divergence test: a replay is a *read*, so two replays agree."""
    journal = _journal(tmp_path)
    kernel = TurnKernel(journal)
    calls: list[dict] = []

    def dispatch(step: int) -> str:
        calls.append({"step": step})
        return f'{{"ok": true, "n": {len(calls)}}}'

    # First pass: two calls, journaled as they happen.
    kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="file_read", args={"path": "a"})
    kernel.after_tool(thread="t1", turn="1", call_id="c1", ok=True, reply=dispatch(1))
    kernel.before_tool(thread="t1", turn="1", call_id="c2", tool="file_read", args={"path": "b"})
    kernel.after_tool(thread="t1", turn="1", call_id="c2", ok=True, reply=dispatch(2))
    first = [kernel.before_tool(thread="t1", turn="1", call_id=c, tool="file_read", args={}).replay for c in ("c1", "c2")]

    # A second pass over the same journal (a resume, a replay, a restart).
    second = [kernel.before_tool(thread="t1", turn="1", call_id=c, tool="file_read", args={}).replay for c in ("c1", "c2")]

    assert first == second
    assert len(calls) == 2, "the second pass must not call anything"
    assert second == ['{"ok": true, "n": 1}', '{"ok": true, "n": 2}']


def test_a_side_effecting_step_that_may_have_run_is_refused_not_repeated(tmp_path: Path):
    """Kill-mid-tool: exactly-once beats at-least-once, and 'unknown' beats a guess."""
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="send_message", side_effecting=True)
    kernel = TurnKernel(journal)

    decision = kernel.before_tool(
        thread="t1", turn="1", call_id="c1", tool="send_message", args={"text": "hi"}, side_effecting=True
    )
    assert decision.uncertain is True
    assert decision.replay is None
    assert "may or may not have completed" in decision.reason

    payload = kernel.uncertain_reply()
    assert '"uncertain": true' in payload
    assert '"ok": false' in payload


def test_a_read_only_step_that_may_have_run_simply_runs(tmp_path: Path):
    """Re-running a lookup is safe, so at-least-once is the better answer there."""
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="file_read", side_effecting=False)
    kernel = TurnKernel(journal)

    decision = kernel.before_tool(
        thread="t1", turn="1", call_id="c1", tool="file_read", args={"path": "a"}, side_effecting=False
    )
    assert decision.action == "run"
    assert decision.replay is None


def test_an_interrupted_step_re_enters_rather_than_being_refused(tmp_path: Path):
    """The approval resume path: the owner said yes, so the step runs."""
    journal = _journal(tmp_path)
    kernel = TurnKernel(journal)
    kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="forget", args={"query": "lease"})
    kernel.interrupted(thread="t1", turn="1", call_id="c1")

    decision = kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="forget", args={"query": "lease"})
    assert decision.action == "run"
    assert "waiting for approval" in decision.reason


def test_the_boundary_records_the_argument_digest(tmp_path: Path):
    """The *same* digest the approval envelope uses, so the two bindings agree.

    A journal that hashed arguments differently from `approval.effective_digest`
    would be recording a second, subtly different notion of "this exact call".
    """
    journal = _journal(tmp_path)
    kernel = TurnKernel(journal)
    args = {"path": "a", "content": "b"}
    kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="file_write", args=args)
    (step,) = journal.turn("t1", "1").steps.values()

    assert step.digest == effective_digest(args)
    assert re.fullmatch(r"[0-9a-f]{16}", step.digest)
    assert step.digest != effective_digest({**args, "content": "c"}), "the digest pins the arguments"


def test_a_kernel_without_a_journal_still_works(tmp_path: Path):
    """A library caller with a bare Runtime gets no durability, not a crash."""
    kernel = TurnKernel(None)
    decision = kernel.before_tool(thread="t1", turn="1", call_id="c1", tool="remember", args={})
    assert decision.action == "run"
    assert kernel.pending() == []
    kernel.after_tool(thread="t1", turn="1", call_id="c1", ok=True, reply="{}")  # no-op, no raise


def test_side_effecting_follows_the_declared_class():
    """The same classes the guard chain uses, so 'may act twice' and 'needs approval' agree."""
    assert side_effecting("memory_search") is False  # read
    assert side_effecting("web_search") is False  # network
    assert side_effecting("remember") is True  # memory_write
    assert side_effecting("send_message") is True  # delivery
    assert side_effecting("computer") is True  # control
    assert side_effecting("some-plugin/tool") is True  # unknown: assume the worst


def test_a_turn_label_other_than_the_default_is_isolated(tmp_path: Path):
    """Two turns of one thread keep their own steps, so a later turn cannot replay the earlier."""
    journal = _journal(tmp_path)
    journal.tool_start("t1", "1", call_id="c1", tool="remember")
    journal.tool_end("t1", "1", call_id="c1", ok=True, result="first")
    assert journal.turn("t1", "1").steps["c1"].result == "first"
    assert journal.turn("t1", "2").steps == {}
