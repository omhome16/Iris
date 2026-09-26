"""Model-free retrieval metrics — the deterministic half of gating.

The vault's two-speed gating idea: deterministic retrieval metrics run on every
change, and judge suites (which need a model and therefore a key, a budget and a
calibration study) run later. This module is the first half.

Everything here is arithmetic over a *ranking* and a set of labelled-relevant
ids, so it needs no database, no model and no network: the same inputs give the
same numbers forever. That is the point — a metric that can drift on its own
cannot gate anything.

Two metrics, because they disagree usefully:

- **recall@k** — did the relevant items make the cut? A retriever that finds the
  right memory but ranks it 6th fails here, which is the failure a person
  notices ("she forgot what I told her").
- **nDCG@k** — were they *near the top*? Recall@k is insensitive to order inside
  the top k, so a ranked list that puts the answer last scores the same as one
  that puts it first. nDCG is what catches that.

Chunk identity is `path::chunk_index`, the pair the index already keys on
(`forget_entry`, `MemoryHit`), so a fixture's labels are the same coordinates the
runtime uses to address a memory. It is written as a function rather than a
property so an id is always constructed the same way in the label file and in
the harness.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


def chunk_id(path: str, chunk_index: int) -> str:
    """The stable id of one indexed chunk: `memory/2026-01-04.md::3`."""
    return f"{path}::{int(chunk_index)}"


def recall_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Fraction of the labelled-relevant ids that appear in the top `k`.

    Denominator is the *labelled* count, not `k`: a corpus where only one chunk
    is relevant should not be scored as if five were expected.
    """
    if k <= 0 or not relevant:
        return 1.0 if not relevant else 0.0
    top = set(ranked[:k])
    return sum(1 for item in relevant if item in top) / len(relevant)


def ndcg_at_k(ranked: Sequence[str], relevant: Sequence[str], k: int) -> float:
    """Normalized discounted cumulative gain with binary relevance.

    `DCG = Σ rel_i / log2(i + 1)` over the top `k`, divided by the best ordering
    the labels allow. `1.0` is the ideal ranking, `0.0` is "none of the relevant
    items made the top k". With no relevant items the score is `1.0` — nothing
    was missed — rather than a NaN that silently propagates.
    """
    if k <= 0 or not relevant:
        return 1.0
    wanted = set(relevant)
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(ranked[:k], start=1)
        if item in wanted
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(k, len(wanted)) + 1))
    return dcg / ideal if ideal else 1.0


@dataclass(frozen=True, slots=True)
class QueryScore:
    """One labelled query's outcome, kept per query rather than averaged away."""

    query: str
    recall: float
    ndcg: float
    ranked: tuple[str, ...]

    @property
    def worst(self) -> float:
        """The conservative read of this query, for picking which one to print."""
        return min(self.recall, self.ndcg)


def score_queries(
    rankings: Mapping[str, Sequence[str]],
    relevance: Mapping[str, Sequence[str]],
    *,
    k: int,
) -> list[QueryScore]:
    """Score every query in `rankings` (a query missing a label scores 1.0).

    The label map is the fixture's `query -> relevant ids`; a query with no
    entry is a labelling mistake, and rather than raise on it the harness can
    assert over `score.recall == 1.0` for an unlabelled query — see the gate test.
    """
    return [
        QueryScore(
            query=query,
            recall=recall_at_k(ranked, relevance.get(query, ()), k),
            ndcg=ndcg_at_k(ranked, relevance.get(query, ()), k),
            ranked=tuple(ranked),
        )
        for query, ranked in rankings.items()
    ]


def mean_metric(scores: Sequence[QueryScore], metric: str) -> float:
    """`recall` or `ndcg`, averaged over queries (0.0 over an empty set)."""
    if not scores:
        return 0.0
    return sum(getattr(score, metric) for score in scores) / len(scores)


def gate_failures(
    scores: Sequence[QueryScore], *, min_recall: float, min_ndcg: float
) -> list[str]:
    """Per-query failures against the fixture's thresholds, worst first.

    Reported per query rather than as an aggregate delta on purpose: "mean
    recall dropped 0.04" hides which memory stopped being found, and the point
    of a gate is to point at the regression, not at a number.
    """
    failing = [s for s in scores if s.recall < min_recall or s.ndcg < min_ndcg]
    failing.sort(key=lambda score: score.worst)
    return [
        f"{score.query!r} (recall@k {score.recall:.2f} < {min_recall:.2f}, "
        f"ndcg@k {score.ndcg:.2f} < {min_ndcg:.2f}) — ranked: {', '.join(score.ranked)}"
        for score in failing
    ]
