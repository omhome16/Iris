"""Eval statistics: intervals, power and judge calibration (audit G6)."""

from __future__ import annotations

import pytest

from iris_ai.eval.stats import (
    DecisionRule,
    bootstrap_mean_ci,
    cohens_kappa,
    decide,
    noise_floor,
    paired_difference_ci,
    raw_agreement,
    samples_needed,
    wilson_interval,
)

# ── Wilson interval ─────────────────────────────────────────────────────────


def test_a_coin_flip_rate_brackets_one_half():
    low, high = wilson_interval(3, 6)
    assert low < 0.5 < high


def test_a_perfect_score_still_has_a_lower_bound():
    """6/6 is not certainty: the interval must say so."""
    low, high = wilson_interval(6, 6)
    assert low > 0.5
    assert high == 1.0


def test_a_zero_score_has_an_upper_bound():
    low, high = wilson_interval(0, 6)
    assert low == 0.0
    assert high < 0.5


def test_an_empty_set_has_no_interval():
    assert wilson_interval(0, 0) == (0.0, 0.0)


# ── bootstrap ───────────────────────────────────────────────────────────────


def test_the_bootstrap_is_deterministic_for_a_seed():
    values = [0.4, 0.6, 0.5, 0.7, 0.3]
    assert bootstrap_mean_ci(values) == bootstrap_mean_ci(values)


def test_the_bootstrap_interval_contains_the_mean():
    values = [0.1, 0.9, 0.5, 0.4, 0.6, 0.55]
    low, high = bootstrap_mean_ci(values)
    assert low <= sum(values) / len(values) <= high


def test_paired_differences_ignore_shared_difficulty():
    baseline = [0.2, 0.3, 0.4, 0.5]
    candidate = [0.25, 0.35, 0.45, 0.55]  # a constant +0.05
    low, high = paired_difference_ci(baseline, candidate)
    assert low > 0.0
    assert high == pytest.approx(0.05, abs=1e-9)


def test_an_empty_comparison_is_zero():
    assert paired_difference_ci([], []) == (0.0, 0.0)


# ── power ───────────────────────────────────────────────────────────────────


def test_the_vault_number_is_derived_not_asserted():
    """Δ=0.02 at σ=0.04 → 63 items per arm."""
    assert samples_needed(0.02, 0.04) == 63


def test_a_bigger_effect_needs_fewer_samples():
    assert samples_needed(0.05, 0.04) < samples_needed(0.02, 0.04)


def test_more_noise_needs_more_samples():
    assert samples_needed(0.02, 0.08) > samples_needed(0.02, 0.04)


def test_a_zero_effect_is_unanswerable():
    assert samples_needed(0.0, 0.04) == 0


# ── noise floor ─────────────────────────────────────────────────────────────


def test_a_single_run_has_no_measured_floor():
    floor = noise_floor([0.8])
    assert floor["measured"] is False
    assert floor["sd"] == 0.0


def test_the_noise_floor_reports_the_spread_of_repeats():
    floor = noise_floor([0.80, 0.84, 0.82])
    assert floor["measured"] is True
    assert floor["runs"] == 3
    assert floor["half_width"] > 0
    assert 0.80 < floor["mean"] < 0.84


# ── judge calibration ───────────────────────────────────────────────────────


def test_perfect_agreement_is_kappa_one():
    assert cohens_kappa([1, 0, 1], [1, 0, 1]) == pytest.approx(1.0)


def test_agreement_at_chance_is_kappa_zero():
    # a constant judge agreeing 3/4 of the time on a 3/4-majority label
    assert cohens_kappa([1, 1, 1, 1], [1, 1, 0, 1]) == pytest.approx(0.0)


def test_raw_agreement_and_kappa_differ_when_chance_is_high():
    judge = [1, 1, 1, 1, 1, 1]
    human = [1, 1, 1, 1, 1, 0]
    assert raw_agreement(judge, human) == pytest.approx(5 / 6)
    assert cohens_kappa(judge, human) == pytest.approx(0.0)


def test_uncalibrated_agreement_is_not_trusted():
    """The vault's rule made concrete: high raw agreement, κ near zero."""
    judge = [1] * 9 + [0]
    human = [1] * 9 + [1]
    assert raw_agreement(judge, human) == pytest.approx(0.9)
    assert cohens_kappa(judge, human) == pytest.approx(0.0)


# ── the pre-registered decision rule ────────────────────────────────────────


def test_a_clear_improvement_passes():
    baseline = [0.5] * 12
    candidate = [0.6] * 12
    verdict = decide(baseline=baseline, candidate=candidate, rule=DecisionRule("recall@5"))
    assert verdict["verdict"] == "pass"
    assert verdict["delta"] == pytest.approx(0.1)


def test_a_regression_fails():
    baseline = [0.5] * 12
    candidate = [0.4] * 12
    verdict = decide(baseline=baseline, candidate=candidate, rule=DecisionRule("recall@5"))
    assert verdict["verdict"] == "fail"


def test_an_ambiguous_result_is_inconclusive_not_a_pass():
    baseline = [0.5] * 10
    candidate = [0.51, 0.49] * 5  # mean delta ~0
    verdict = decide(baseline=baseline, candidate=candidate, rule=DecisionRule("recall@5"))
    assert verdict["verdict"] == "inconclusive"
    assert verdict["ci"][0] <= 0 <= verdict["ci"][1]


def test_a_decrease_rule_passes_on_a_clean_drop():
    baseline = [10.0, 11.0, 12.0, 10.5, 11.5]
    candidate = [5.0, 6.0, 7.0, 5.5, 6.5]
    rule = DecisionRule("latency_s", direction="decrease", min_effect=0.5)
    assert decide(baseline=baseline, candidate=candidate, rule=rule)["verdict"] == "pass"


def test_an_invalid_direction_is_rejected_at_construction():
    with pytest.raises(ValueError, match="direction"):
        DecisionRule("recall@5", direction="sideways")


# ── the report itself (audit G6, task 7) ────────────────────────────────────


def _eval_lab():
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("eval_lab", root / "scripts" / "eval_lab.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_report_carries_intervals_not_points():
    lab = _eval_lab()
    modes = list(lab.MODES)
    results = {m: {"recall@5": 5 / 6, "mrr@5": 0.7, "mean_rank": 2.0} for m in modes}
    per_query = {m: [1.0, 1.0, 1.0, 1.0, 1.0, 0.0] for m in modes}
    report = lab.render_report(results, per_query, {}, [0.9, 0.8], [0.7, 0.6], ["a query"])

    assert "recall@5 (95% CI)" in report  # the rate carries its interval
    assert "| mode | Δ recall@5 vs full | 95% CI | verdict |" in report
    assert "inconclusive" in report  # a 6-query set cannot pass a pre-registered rule
    assert "queries per arm" in report  # the power statement is in the report
    assert "a query" in report


def test_the_report_is_a_pure_function_of_its_measurements():
    lab = _eval_lab()
    modes = list(lab.MODES)
    results = {m: {"recall@5": 0.5, "mrr@5": 0.5, "mean_rank": 3.0} for m in modes}
    per_query = {m: [1.0, 0.0, 1.0, 0.0, 1.0, 0.0] for m in modes}
    first = lab.render_report(results, per_query, {}, [0.5], [0.5], [])
    second = lab.render_report(results, per_query, {}, [0.5], [0.5], [])
    assert first == second
