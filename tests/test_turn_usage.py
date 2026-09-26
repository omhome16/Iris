"""P5 — per-turn token accounting.

Multi-agent systems use roughly 15x the tokens of a chat. That number only
changes a decision if it is visible *while the turn runs*, so this suite pins the
chain end to end: the LLM client's recording path feeds a turn-scoped
accumulator, the accumulator survives having no cost ledger configured, it
disappears cleanly outside a turn, and a role run reports the tokens it spent.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from iris import turnlog
from iris.agents.roles import RESEARCHER
from iris.agents.runner import RoleRunner
from iris.memory.files import WorkspaceFiles
from iris.memory.llm import LLMClient
from test_agent_graph import make_runtime


def _usage(prompt: int, completion: int, cached: int = 0) -> SimpleNamespace:
    """A provider usage object. `cached` mirrors the OpenAI/Gemini shapes, which
    report cached prompt tokens nested rather than as a top-level field."""
    details = SimpleNamespace(cached_tokens=cached) if cached else None
    return SimpleNamespace(
        prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=details
    )


class CountingLLM(LLMClient):
    """Reports usage through the client's own recording path, like a real call."""

    def __init__(self, ledger=None) -> None:
        super().__init__(ledger)
        self.calls = 0

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        tier = str(kwargs.get("tier", "strong"))
        self._record("fake-model", tier, _usage(10, 5))
        return "Report: found it.", [], ""


# ── the accumulator ──────────────────────────────────────────────────────


def test_nothing_is_accumulated_outside_a_turn():
    turnlog.add_usage(tier="cheap", prompt_tokens=100, completion_tokens=50)
    assert turnlog.usage_total() == 0
    assert turnlog.usage_snapshot() == {}


def test_usage_accumulates_per_tier_inside_a_turn():
    with turnlog.collect() as log:
        turnlog.add_usage(tier="cheap", model="m-cheap", prompt_tokens=10, completion_tokens=5)
        turnlog.add_usage(tier="cheap", model="m-cheap", prompt_tokens=10, completion_tokens=5)
        turnlog.add_usage(tier="strong", model="m-strong", prompt_tokens=100, completion_tokens=20)
        assert turnlog.usage_total() == 150
        assert log.usage["cheap"]["calls"] == 2
        assert log.usage["cheap"]["prompt_tokens"] == 20
        assert log.usage["strong"]["prompt_tokens"] == 100


def test_the_trace_carries_the_spend():
    with turnlog.collect() as log:
        turnlog.add_usage(tier="strong", model="m", prompt_tokens=7, completion_tokens=3)
    trace = log.to_trace()
    assert trace["usage"]["strong"]["calls"] == 1
    assert trace["total_tokens"] == 10
    assert trace["models"]["strong"] == ["m"]


def test_a_turn_with_no_model_calls_stays_a_compact_trace():
    with turnlog.collect() as log:
        turnlog.record("something", value=1)
    assert "usage" not in log.to_trace()


def test_recording_usage_never_raises():
    """Telemetry must not cost a reply, even from a bad call site."""
    with turnlog.collect():
        turnlog.add_usage(tier="strong", prompt_tokens="not-a-number")  # type: ignore[arg-type]
    # The bad value is swallowed by the guard rather than propagating.
    assert turnlog.usage_total() == 0


# ── the client's recording path ──────────────────────────────────────────


def test_the_client_reports_usage_into_the_active_turn():
    """The accumulator must not depend on a cost ledger being configured — a
    turn with no ledger still needs its token count."""
    client = LLMClient(ledger=None)
    with turnlog.collect() as log:
        client._record("fake-model", "cheap", _usage(30, 12))
    assert log.usage["cheap"] == {
        "calls": 1,
        "prompt_tokens": 30,
        "completion_tokens": 12,
        "cached_tokens": 0,
    }


def test_cached_prompt_tokens_reach_the_accumulator():
    """The day budget's CACHED bucket needs a source: providers report cached
    prompt tokens (OpenAI-style `prompt_tokens_details`), and the client is the
    one place that knows, so it passes them into the turn log."""
    client = LLMClient(ledger=None)
    with turnlog.collect() as log:
        client._record("fake-model", "strong", _usage(100, 20, cached=64))
        assert log.cached_tokens() == 64
        assert turnlog.usage_total() == 120  # cached tokens are still prompt tokens


def test_the_ledger_still_receives_its_record(tmp_path: Path):
    """The accumulator observes; it does not replace accounting."""
    from iris.ledger import CostLedger

    ledger = CostLedger(tmp_path / "llm_calls.jsonl")
    client = LLMClient(ledger=ledger)
    with turnlog.collect():
        client._record("fake-model", "strong", _usage(100, 40))
    rows = ledger._read()
    assert len(rows) == 1
    assert rows[0]["prompt_tokens"] == 100
    assert rows[0]["tier"] == "strong"


# ── a role run reports what it spent ─────────────────────────────────────


async def test_a_role_run_reports_its_tokens(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, CountingLLM())
    with turnlog.collect():
        handoff = await RoleRunner(runtime, RESEARCHER).run("where was the trip?")
    assert handoff.spend.tokens == 15  # one call: 10 prompt + 5 completion
    assert handoff.spend.ms >= 0


def test_a_run_outside_a_turn_reports_zero_tokens(tmp_path: Path):
    """No turn, no accumulator: a direct call reports 0 rather than raising."""
    import asyncio

    files = WorkspaceFiles(tmp_path)
    runtime = make_runtime(files, CountingLLM())
    handoff = asyncio.run(RoleRunner(runtime, RESEARCHER).run("q"))
    assert handoff.spend.tokens == 0
