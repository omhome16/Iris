"""Pre-tool guards: spiral/dedup and the cascade breaker (audit G1, G2)."""

from __future__ import annotations

from iris_ai.budget import Budget, BudgetPolicy, CounterKind
from iris_ai.guards import (
    ALLOW,
    GuardChain,
    SpiralDetector,
    arg_similarity,
    arg_tokens,
    normalised_args,
)
from iris_ai.guards import (
    CircuitBreaker as Breaker,
)

# ── argument similarity ────────────────────────────────────────────────────


def test_identical_arguments_are_maximally_similar():
    assert arg_similarity({"q": "rust"}, {"q": "rust"}) == 1.0


def test_case_and_whitespace_do_not_disguise_a_loop():
    assert arg_similarity({"q": " Rust "}, {"q": "rust"}) == 1.0


def test_disjoint_arguments_share_nothing():
    assert arg_similarity({"q": "rust"}, {"q": "cilantro"}) == 0.0


def test_nested_arguments_are_compared_leaf_by_leaf():
    left = {"filters": {"lang": "rust", "year": 2024}}
    right = {"filters": {"lang": "rust", "year": 2025}}
    assert 0.0 < arg_similarity(left, right) < 1.0
    assert arg_tokens(left) == {"filters.lang=rust", "filters.year=2024"}


def test_normalised_args_is_stable_and_never_a_raw_dump_error():
    assert normalised_args({"b": 1, "a": 2}) == normalised_args({"a": 2, "b": 1})
    assert normalised_args(object())  # never raises


# ── the spiral detector ────────────────────────────────────────────────────


def test_the_same_call_three_times_is_refused_on_the_third():
    spiral = SpiralDetector(min_repeats=3, jaccard=0.72, max_calls=8)
    assert spiral.note("memory_search", {"query": "rust"}).allowed
    assert spiral.note("memory_search", {"query": "rust"}).allowed
    verdict = spiral.note("memory_search", {"query": "rust"})
    assert verdict.refused
    assert verdict.guard == "spiral"
    assert "near-identical" in verdict.reason


def test_varying_arguments_are_not_a_spiral():
    spiral = SpiralDetector(min_repeats=3, jaccard=0.72, max_calls=8)
    for query in ("rust", "cilantro", "piano", "delhi"):
        assert spiral.note("memory_search", {"query": query}).allowed


def test_a_different_tool_is_not_a_repeat_of_another():
    spiral = SpiralDetector(min_repeats=3, jaccard=0.72, max_calls=8)
    assert spiral.note("memory_search", {"query": "rust"}).allowed
    assert spiral.note("file_read", {"query": "rust"}).allowed
    assert spiral.note("web_search", {"query": "rust"}).allowed


def test_the_growth_ceiling_stops_a_turn_whatever_it_calls():
    spiral = SpiralDetector(min_repeats=3, jaccard=0.72, max_calls=3)
    assert spiral.note("a", {"i": 1}).allowed
    assert spiral.note("b", {"i": 2}).allowed
    assert spiral.note("c", {"i": 3}).allowed
    verdict = spiral.note("d", {"i": 4})
    assert verdict.refused
    assert "ceiling" in verdict.reason


# ── the cascade breaker ────────────────────────────────────────────────────


def test_two_consecutive_failures_open_the_circuit():
    breaker = Breaker(failure_threshold=2, failing_tools_per_turn=3)
    breaker.note("skill_run", ok=False)
    assert breaker.check("skill_run").allowed  # one failure is not a streak
    breaker.note("skill_run", ok=False)
    verdict = breaker.check("skill_run")
    assert verdict.refused
    assert verdict.guard == "circuit"
    assert "do not retry" in verdict.reason


def test_a_success_resets_the_streak():
    breaker = Breaker(failure_threshold=2, failing_tools_per_turn=3)
    breaker.note("skill_run", ok=False)
    breaker.note("skill_run", ok=True)
    breaker.note("skill_run", ok=False)
    assert breaker.check("skill_run").allowed


def test_three_distinct_failing_tools_escalate_the_turn():
    breaker = Breaker(failure_threshold=2, failing_tools_per_turn=3)
    breaker.note("a", ok=False)
    breaker.note("b", ok=False)
    assert breaker.check("c").allowed
    breaker.note("c", ok=False)
    verdict = breaker.check("d")  # a *healthy* tool is refused too
    assert verdict.refused
    assert "different tools have failed" in verdict.reason


# ── the chain ──────────────────────────────────────────────────────────────


def test_the_chain_is_a_no_op_when_disabled():
    chain = GuardChain(enabled=False, max_calls_per_turn=1)
    assert chain.before("x", {"a": 1}).allowed
    assert chain.before("x", {"a": 1}).allowed
    assert chain.before("x", {"a": 1}).allowed


def test_the_budget_guard_fires_before_the_spiral_guard():
    budget = Budget(BudgetPolicy(max_tokens_per_day=10))
    budget.note_tokens(CounterKind.OUTPUT, 10)
    chain = GuardChain(budget=budget, max_calls_per_turn=8)
    verdict = chain.before("memory_search", {"query": "rust"})
    assert verdict.refused
    assert verdict.guard == "budget"


def test_the_circuit_guard_fires_before_the_spiral_guard():
    chain = GuardChain(failure_threshold=1, max_calls_per_turn=8)
    chain.after("skill_run", ok=False)
    verdict = chain.before("skill_run", {"name": "x"})
    assert verdict.refused
    assert verdict.guard == "circuit"


def test_reset_turn_clears_the_turn_scoped_state_only():
    budget = Budget(BudgetPolicy(max_tokens_per_day=100))
    budget.note_tokens(CounterKind.OUTPUT, 40)
    chain = GuardChain(budget=budget, max_calls_per_turn=3, failure_threshold=2)
    chain.before("a", {"i": 1})
    chain.after("a", ok=False)
    chain.after("a", ok=False)
    chain.reset_turn()
    # the turn-scoped state is gone...
    assert chain.before("b", {"i": 2}).allowed
    # ...and the day-scoped budget is not
    assert chain.budget.total() == 40


def test_a_runaway_loop_is_refused_not_merely_observed():
    """The CI simulation: 20 identical calls in one turn, asserting the
    *refusal*. Design's numbers: unchecked ≈ $2 / 30 s, circuit-broken ≈ $0.01."""
    chain = GuardChain(max_calls_per_turn=20, spiral_min_repeats=3, spiral_jaccard=0.72)
    verdicts = [chain.before("memory_search", {"query": "same"}) for _ in range(20)]
    refusals = [v for v in verdicts if v.refused]
    assert len(refusals) == 18  # only the first two got through
    assert {v.guard for v in refusals} == {"spiral"}


def test_a_retry_storm_is_refused_by_the_circuit_not_the_spiral_guard():
    chain = GuardChain(max_calls_per_turn=20, failure_threshold=2)
    assert chain.before("skill_run", {"name": "a"}).allowed
    chain.after("skill_run", ok=False)
    assert chain.before("skill_run", {"name": "b"}).allowed
    chain.after("skill_run", ok=False)
    verdict = chain.before("skill_run", {"name": "c"})
    assert verdict.refused
    assert verdict.guard == "circuit"


def test_recording_a_verdict_outside_a_turn_is_a_no_op():
    """Tools and tests call this freely; outside a turn it must not raise."""
    chain = GuardChain(max_calls_per_turn=1)
    chain.record(ALLOW, "x", {})
    chain.record(chain.before("x", {"a": 1}), "x", {"a": 1})
