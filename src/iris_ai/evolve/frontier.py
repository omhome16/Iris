"""Pareto frontier and the leakage check `iris evolve` runs after the search."""

from __future__ import annotations


def pareto(rows: list[dict], *, metric: str = "score", cost: str = "tokens") -> list[dict]:
    """Keep a row when no other row is better on the metric and cheaper on tokens."""
    kept = []
    for row in rows:
        dominated = False
        for other in rows:
            if other is row:
                continue
            better = float(other.get(metric) or 0) >= float(row.get(metric) or 0)
            cheaper = float(other.get(cost) or 0) <= float(row.get(cost) or 0)
            strictly = float(other.get(metric) or 0) > float(row.get(metric) or 0) or float(
                other.get(cost) or 0
            ) < float(row.get(cost) or 0)
            if better and cheaper and strictly:
                dominated = True
                break
        if not dominated:
            kept.append(row)
    return kept


def leaks(source: str, cases: list[str]) -> list[str]:
    """Case strings that were copied into a candidate. The proposer must not see them."""
    folded = source.lower()
    return [case for case in cases if case and case.lower() in folded]
