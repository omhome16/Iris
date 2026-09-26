"""Budgets: scoped ceilings with counters split by kind (audit G3)."""

from __future__ import annotations

from iris_ai.budget import BUDGET_POLICY_VERSION, Budget, BudgetPolicy, CounterKind


def test_counters_are_split_by_kind_not_one_total():
    budget = Budget()
    budget.note_tokens(CounterKind.INPUT, 100)
    budget.note_tokens(CounterKind.OUTPUT, 40)
    budget.note_tokens(CounterKind.CACHED, 500)
    budget.note_tokens(CounterKind.EMBEDDING, 7)
    snapshot = budget.snapshot()["counters"]
    assert snapshot["input"] == 100
    assert snapshot["output"] == 40
    assert snapshot["cached"] == 500
    assert snapshot["embedding"] == 7
    assert budget.total() == 647


def test_the_embedding_tier_is_counted_as_embeddings_not_conversation():
    budget = Budget()
    budget.note_usage(
        {
            "strong": {
                "calls": 1,
                "prompt_tokens": 900,
                "completion_tokens": 120,
                "cached_tokens": 200,
            },
            "embedding": {
                "calls": 3,
                "prompt_tokens": 60,
                "completion_tokens": 0,
                "cached_tokens": 0,
            },
        }
    )
    counters = budget.snapshot()["counters"]
    assert counters["input"] == 900
    assert counters["output"] == 120
    assert counters["embedding"] == 60
    assert counters["cached"] == 200


def test_a_zero_ceiling_means_no_ceiling():
    budget = Budget(BudgetPolicy(max_tokens_per_turn=0, max_tokens_per_day=0))
    budget.note_tokens(CounterKind.INPUT, 10_000_000)
    assert budget.refusal(turn_tokens=10_000_000) == ""
    assert not budget.policy.enforced


def test_the_per_turn_ceiling_refuses_and_names_itself():
    budget = Budget(BudgetPolicy(max_tokens_per_turn=1000))
    budget.note_tokens(CounterKind.INPUT, 999)
    assert budget.refusal(turn_tokens=999) == ""
    reason = budget.refusal(turn_tokens=1000)
    assert "per-turn ceiling" in reason
    assert "1000" in reason


def test_the_day_ceiling_survives_a_new_turn():
    """The whole point of the day scope: a runaway that spends a little every
    turn is bounded across turns, not just within one."""
    budget = Budget(BudgetPolicy(max_tokens_per_day=100))
    budget.note_tokens(CounterKind.OUTPUT, 100)
    assert "today's token ceiling" in budget.refusal(turn_tokens=0)


def test_the_day_rolls_reset_the_counters():
    budget = Budget(BudgetPolicy(max_tokens_per_day=100), today="2026-09-24")
    budget.note_tokens(CounterKind.OUTPUT, 100)
    assert budget.refusal() != ""
    assert budget.roll_day("2026-09-25") is True
    assert budget.refusal() == ""
    assert budget.snapshot()["day"] == "2026-09-25"
    # the same day is not a roll
    assert budget.roll_day("2026-09-25") is False


def test_persistence_round_trips_the_day_counters(tmp_path):
    path = tmp_path / "config" / "budget.json"
    first = Budget(BudgetPolicy(max_tokens_per_day=100), path=path)
    first.note_tokens(CounterKind.INPUT, 77)
    second = Budget(BudgetPolicy(max_tokens_per_day=100), path=path)
    assert second.snapshot()["counters"]["input"] == 77
    assert second.refusal() == ""


def test_a_stale_day_file_is_not_todays_spend(tmp_path):
    path = tmp_path / "config" / "budget.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"date": "2020-01-01", "counters": {"input": 99999}}', encoding="utf-8")
    budget = Budget(BudgetPolicy(max_tokens_per_day=100), path=path)
    assert budget.total() == 0
    assert budget.refusal() == ""


def test_an_unwritable_budget_file_is_reported_not_raised(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    budget = Budget(BudgetPolicy(max_tokens_per_day=100), path=blocker / "config" / "budget.json")
    budget.note_tokens(CounterKind.INPUT, 5)
    assert budget.persistence_error
    assert budget.refusal() == ""  # still enforced in memory


def test_the_policy_version_is_recorded_with_the_numbers():
    snapshot = Budget().snapshot()
    assert snapshot["policy"]["version"] == BUDGET_POLICY_VERSION


def test_from_settings_reads_the_declared_ceiling():
    policy = BudgetPolicy.from_settings()
    assert isinstance(policy.max_tokens_per_day, int)
    assert policy.version
