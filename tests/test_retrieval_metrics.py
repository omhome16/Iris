"""recall@k / nDCG@k — pure arithmetic, no database and no model.

The gate in `test_retrieval_gate.py` needs Postgres; this file does not, so the
metric maths is checked everywhere (a CI job with no services, a laptop, a
release build). That matters because these two functions decide whether the gate
passes: a bug here either hides a regression or invents one, and both failures
look identical from the outside.
"""

from __future__ import annotations

import math

import pytest

from iris_ai.eval.retrieval import (
    QueryScore,
    chunk_id,
    gate_failures,
    mean_metric,
    ndcg_at_k,
    recall_at_k,
    score_queries,
)


def test_chunk_id_matches_the_coordinates_the_index_keys_on():
    assert chunk_id("memory/2026-01-04.md", 3) == "memory/2026-01-04.md::3"
    assert chunk_id("MEMORY.md", 0) == "MEMORY.md::0"


def test_recall_counts_against_the_labels_not_against_k():
    """One labelled fact in a top-3 is 1.0, not 1/3: the denominator is what the
    fixture says is relevant, otherwise every query would be scored against a
    set it never had."""
    assert recall_at_k(["a", "b", "c"], ["a"], 3) == 1.0
    assert recall_at_k(["a", "b", "c"], ["a", "d"], 3) == 0.5
    assert recall_at_k(["b", "c", "d"], ["a"], 3) == 0.0


def test_recall_ignores_position_which_is_why_ndcg_exists():
    assert recall_at_k(["x", "y", "a"], ["a"], 3) == 1.0
    assert ndcg_at_k(["x", "y", "a"], ["a"], 3) < ndcg_at_k(["a", "y", "x"], ["a"], 3)


def test_ndcg_is_one_for_the_ideal_ordering_and_zero_when_nothing_is_found():
    assert ndcg_at_k(["a", "b", "c"], ["a", "b"], 3) == pytest.approx(1.0)
    assert ndcg_at_k(["x", "y", "z"], ["a"], 3) == 0.0
    # Rank 2 of a single-label query: DCG 1/log2(3), IDCG 1/log2(2).
    assert ndcg_at_k(["x", "a", "z"], ["a"], 3) == pytest.approx(1 / math.log2(3))


def test_ndcg_is_one_when_there_is_nothing_to_find():
    """No labels means nothing was missed. Returning 0.0 would quietly punish a
    query that has no right answer, and NaN would poison the mean."""
    assert ndcg_at_k(["a", "b"], [], 3) == 1.0
    assert recall_at_k(["a", "b"], [], 3) == 1.0


def test_scores_are_kept_per_query_not_averaged_away():
    rankings = {"easy": ["a"], "hard": ["x", "y", "z"]}
    relevance = {"easy": ["a"], "hard": ["b"]}
    scores = score_queries(rankings, relevance, k=3)
    by_query = {s.query: s for s in scores}
    assert by_query["easy"].recall == 1.0
    assert by_query["hard"].recall == 0.0
    assert mean_metric(scores, "recall") == 0.5
    assert mean_metric([], "recall") == 0.0


def test_an_unlabelled_query_scores_perfectly_which_is_why_the_gate_checks_labels():
    """Not a recommendation — a documented hazard. The gate test asserts every
    query carries a label, because this default would otherwise let a fixture
    edit quietly loosen the gate instead of failing it."""
    scores = score_queries({"forgot to label this": ["x"]}, {}, k=3)
    assert scores[0].recall == 1.0 and scores[0].ndcg == 1.0


def test_failures_name_the_query_the_metrics_and_the_ranking():
    scores = [
        QueryScore(query="good", recall=1.0, ndcg=1.0, ranked=("a",)),
        QueryScore(query="bad", recall=0.0, ndcg=0.0, ranked=("x", "y", "z")),
        QueryScore(query="shallow", recall=1.0, ndcg=0.5, ranked=("x", "a")),
    ]
    failures = gate_failures(scores, min_recall=1.0, min_ndcg=0.9)
    assert len(failures) == 2
    assert "bad" in failures[0], "worst first, so the first line is the one to read"
    assert "shallow" in failures[1]
    assert "x, y, z" in failures[0], "the ranking it actually produced is the evidence"


def test_a_failure_reports_both_metrics_so_a_shallow_match_is_visible():
    scores = [QueryScore(query="q", recall=1.0, ndcg=0.63, ranked=("x", "a"))]
    failures = gate_failures(scores, min_recall=1.0, min_ndcg=0.9)
    assert "ndcg@k 0.63 < 0.90" in failures[0]
    assert "recall@k 1.00 < 1.00" in failures[0]


def test_thresholds_are_inclusive_at_the_boundary():
    scores = [QueryScore(query="q", recall=1.0, ndcg=0.9, ranked=("a",))]
    assert gate_failures(scores, min_recall=1.0, min_ndcg=0.9) == []
