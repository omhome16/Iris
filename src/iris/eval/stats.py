"""Statistics for eval gates — intervals, power and agreement.

The vault's eval note is blunt: an eval that reports a point estimate on a small
set is a coin flip with a chart. The discipline it asks for is small and
mechanical, and all of it lives here:

1. **An interval, not a point.** Wilson for a rate (recall@k is a proportion of
   queries), bootstrap for a mean. Both are exact enough at eval-set sizes and
   need no dependency.
2. **A noise floor.** Run the same configuration 3–5× and report the spread. If
   the floor is larger than the effect you are chasing, the experiment cannot
   answer the question — and that is a result, not a failure.
3. **Sample sizing before the run.** `samples_needed(Δ, σ)` is the number the
   vault quotes (~63 per arm for Δ=0.02 at σ=0.04). Computing it beats asserting
   it, because it exposes how big a set the claim needs.
4. **A pre-registered decision rule.** Written down before the run: the direction,
   the minimum effect, the alpha. A rule chosen after seeing the numbers is how
   every ablation "wins".
5. **Judge–human agreement.** Cohen's κ before the judge is trusted, because an
   uncalibrated judge is a random number generator with good manners.

Everything is deterministic: bootstrap resampling uses a fixed seed, so a report
is reproducible.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import NormalDist, fmean, stdev


def _z(confidence: float) -> float:
    """Two-sided standard-normal quantile for a confidence level."""
    return NormalDist().inv_cdf(1 - (1 - confidence) / 2)


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile over an already-sorted sequence."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[int(position)])
    fraction = position - lower
    return float(sorted_values[lower] * (1 - fraction) + sorted_values[upper] * fraction)


# ── intervals ───────────────────────────────────────────────────────────────


def wilson_interval(successes: int, n: int, *, confidence: float = 0.95) -> tuple[float, float]:
    """A CI for a proportion that behaves at small n and near 0 or 1.

    Preferred over the normal approximation because eval sets are small and the
    interesting results are exactly the extreme ones (a mode that hits 6/6 or 0/6).
    """
    if n <= 0:
        return (0.0, 0.0)
    z = _z(confidence)
    p = successes / n
    denominator = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return (max(0.0, center - half), min(1.0, center + half))


def bootstrap_mean_ci(
    values: Sequence[float], *, confidence: float = 0.95, resamples: int = 2000, seed: int = 0
) -> tuple[float, float]:
    """A percentile bootstrap CI for a mean. Deterministic for a given seed."""
    data = [float(v) for v in values]
    if not data:
        return (0.0, 0.0)
    if len(data) == 1:
        return (data[0], data[0])
    rng = random.Random(seed)
    n = len(data)
    means = sorted(sum(data[rng.randrange(n)] for _ in range(n)) / n for _ in range(max(1, resamples)))
    return (_quantile(means, (1 - confidence) / 2), _quantile(means, 1 - (1 - confidence) / 2))


def paired_difference_ci(
    baseline: Sequence[float],
    candidate: Sequence[float],
    *,
    confidence: float = 0.95,
    resamples: int = 2000,
    seed: int = 0,
) -> tuple[float, float]:
    """A CI for `mean(candidate - baseline)` over the *same* items.

    Paired, because eval queries are fixed: comparing two modes on the same query
    set removes the query-difficulty variance, which is usually the largest term.
    """
    size = min(len(baseline), len(candidate))
    differences = [float(candidate[i]) - float(baseline[i]) for i in range(size)]
    return bootstrap_mean_ci(differences, confidence=confidence, resamples=resamples, seed=seed)


# ── power ───────────────────────────────────────────────────────────────────


def samples_needed(effect: float, sigma: float, *, alpha: float = 0.05, power: float = 0.8) -> int:
    """Items **per arm** for a two-sided two-sample test.

    `n = 2 (z_{1-α/2} + z_{power})² σ² / Δ²`. With Δ=0.02 and σ=0.04 this returns
    63 — the vault's number, derived rather than memorised. A `0` effect is
    unanswerable at any n, so it returns `0` rather than infinity.
    """
    if effect <= 0:
        return 0
    z_alpha = NormalDist().inv_cdf(1 - alpha / 2)
    z_power = NormalDist().inv_cdf(power)
    n = 2 * ((z_alpha + z_power) ** 2) * (sigma**2) / (effect**2)
    return max(2, math.ceil(n))


# ── the noise floor ─────────────────────────────────────────────────────────


def noise_floor(runs: Sequence[float], *, confidence: float = 0.95) -> dict:
    """The spread of one configuration run 3–5×.

    Returns the mean, standard deviation and the half-width of the CI — the number
    an effect must beat to be believed. A single run has no floor, and saying so is
    better than reporting `0.0`.
    """
    values = [float(v) for v in runs]
    if len(values) < 2:
        return {"runs": len(values), "mean": values[0] if values else 0.0, "sd": 0.0, "half_width": 0.0, "measured": False}
    low, high = bootstrap_mean_ci(values, confidence=confidence)
    return {
        "runs": len(values),
        "mean": fmean(values),
        "sd": stdev(values),
        "half_width": (high - low) / 2,
        "measured": True,
    }


# ── judge calibration ───────────────────────────────────────────────────────


def raw_agreement(judge: Sequence[object], human: Sequence[object]) -> float:
    pairs = list(zip(judge, human, strict=False))
    if not pairs:
        return 0.0
    return sum(1 for a, b in pairs if a == b) / len(pairs)


def cohens_kappa(judge: Sequence[object], human: Sequence[object]) -> float:
    """Agreement corrected for chance. `1.0` is perfect, `0.0` is chance.

    The vault's rule: measure this *before* trusting a judge, because a judge that
    agrees with a human 80% of the time on a 90%-majority label is worse than
    guessing.
    """
    pairs = list(zip(judge, human, strict=False))
    if not pairs:
        return 0.0
    n = len(pairs)
    labels = {value for pair in pairs for value in pair}
    observed = sum(1 for a, b in pairs if a == b) / n
    expected = sum(
        (sum(1 for a, _ in pairs if a == label) / n) * (sum(1 for _, b in pairs if b == label) / n)
        for label in labels
    )
    if expected >= 1.0:
        return 1.0
    return (observed - expected) / (1 - expected)


# ── the pre-registered decision rule ────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DecisionRule:
    """Written down *before* the run: direction, minimum effect, alpha."""

    metric: str
    direction: str = "increase"  # "increase" | "decrease"
    min_effect: float = 0.02
    alpha: float = 0.05

    def __post_init__(self) -> None:
        if self.direction not in ("increase", "decrease"):
            raise ValueError(f"direction must be increase or decrease, not {self.direction!r}")


def decide(*, baseline: Sequence[float], candidate: Sequence[float], rule: DecisionRule) -> dict:
    """Apply the rule to a paired comparison.

    - **pass** — the whole CI is beyond the minimum effect in the declared
      direction, so the claim survives its own uncertainty.
    - **fail** — the whole CI is on the wrong side of zero: a regression.
    - **inconclusive** — everything else, including a real-looking gain whose
      interval still contains the threshold. Reporting "inconclusive" is the
      honest answer, and the one a post-hoc rule would have rounded up.
    """
    delta_ci = paired_difference_ci(baseline, candidate, confidence=1 - rule.alpha)
    deltas = [float(candidate[i]) - float(baseline[i]) for i in range(min(len(baseline), len(candidate)))]
    point = fmean(deltas) if deltas else 0.0
    low, high = delta_ci
    signed_effect = rule.min_effect if rule.direction == "increase" else -rule.min_effect

    if rule.direction == "increase":
        if low > rule.min_effect:
            verdict, reason = "pass", f"CI lower bound {low:+.4f} exceeds the +{rule.min_effect:g} threshold"
        elif high < 0.0:
            verdict, reason = "fail", f"CI upper bound {high:+.4f} is below zero — a regression"
        else:
            verdict, reason = "inconclusive", f"CI [{low:+.4f}, {high:+.4f}] does not clear {signed_effect:+.4f}"
    else:
        if high < -rule.min_effect:
            verdict, reason = "pass", f"CI upper bound {high:+.4f} is below the -{rule.min_effect:g} threshold"
        elif low > 0.0:
            verdict, reason = "fail", f"CI lower bound {low:+.4f} is above zero — a regression"
        else:
            verdict, reason = "inconclusive", f"CI [{low:+.4f}, {high:+.4f}] does not clear {signed_effect:+.4f}"

    return {
        "metric": rule.metric,
        "verdict": verdict,
        "reason": reason,
        "delta": point,
        "ci": delta_ci,
        "threshold": signed_effect,
        "n": len(deltas),
    }
