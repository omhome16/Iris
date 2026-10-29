"""Reflexion retries once. React does not. Success on this suite is a tie."""

from __future__ import annotations

from iris_ai.components import list_options
from iris_ai.engines.dispatch import resolve
from iris_ai.eval.score import compare_suite, list_suites, score_engine
from iris_ai.eval.stats import paired_difference_ci


def test_reflexion_resolves_and_is_listed() -> None:
    assert "reflexion" in list_options("engine")
    engine = resolve("reflexion")
    assert engine.name == "reflexion"
    assert engine.streams() is False
    assert engine.wants_plan({}) is False


def test_reflexion_and_react_tie_on_success_and_differ_on_retries() -> None:
    """Both finish the two scripted tasks, so the success interval includes zero.

    Reflexion critiques and retries once on every case. That retry count is
    the difference, and its interval excludes zero. The noise floor is zero
    because the scorer is deterministic.
    """
    path = next(item for item in list_suites("engine") if item.stem == "tasks")
    success = compare_suite(path, "reflexion", against="react")
    low, high = success["ci"]
    assert success["baseline"] == success["candidate"] == 1.0
    assert low <= 0.0 <= high
    assert success["noise_floor"] == 0.0
    cases = [
        {"input": "Find the note."},
        {"input": "Stop when you know."},
    ]
    base = [score_engine("react", case)["retries"] for case in cases]
    cand = [score_engine("reflexion", case)["retries"] for case in cases]
    retry_low, retry_high = paired_difference_ci(base, cand, seed=0)
    assert base == [0.0, 0.0]
    assert cand == [1.0, 1.0]
    assert retry_low > 0.0 and retry_high > 0.0
