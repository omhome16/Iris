"""Evaluation — measurement discipline, not a leaderboard.

P8's G6 finding was that the eval lab reported point estimates. A point estimate
with no interval is not a measurement: on a six-query set, "recall@5 = 0.83" and
"recall@5 = 0.67" can be the same number.

`iris.eval.stats` holds the arithmetic this needs — confidence intervals, a noise
floor, sample sizing and judge–human agreement — as pure functions over stdlib, so
it is unit-tested without a database or a model.
"""

from __future__ import annotations

from iris.eval.stats import (
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

__all__ = [
    "DecisionRule",
    "bootstrap_mean_ci",
    "cohens_kappa",
    "decide",
    "noise_floor",
    "paired_difference_ci",
    "raw_agreement",
    "samples_needed",
    "wilson_interval",
]
