"""Forgetting — decay curves, supersession and rot detection.

Ebbinghaus-style exponential decay for episodic content (already used at
recall time in `index.recency_weight`); here we expose the *curves* for the
dashboard and flag memory rot: entries past their useful half-life that have
been superseded or never reinforced.

Forgetting is a first-class function: measurable, dashboard-visible,
manually overridable (`/forget`). Nothing is hard-deleted silently — retired
entries carry `(superseded <date>)` markers instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta

from iris.config import settings
from iris.memory.index import MemoryIndex


@dataclass(slots=True)
class RotEntry:
    path: str
    chunk_index: int
    content: str
    retention: float
    age_days: int
    reason: str


def retention_fraction(age_days: int, *, half_life_days: int | None = None) -> float:
    """Ebbinghaus curve: fraction of a memory's strength left after age_days."""
    half_life = half_life_days or settings.recency_half_life_days
    return math.exp(-math.log(2) * max(0, age_days) / half_life)


def decay_curve(*, span_days: int = 90, half_life_days: int | None = None) -> list[tuple[int, float]]:
    """Dashboard series: (day, retention) pairs."""
    return [(d, round(retention_fraction(d, half_life_days=half_life_days), 4)) for d in range(span_days + 1)]


class ForgettingEngine:
    """Detects rot and computes retention stats over the index."""

    def __init__(self, index: MemoryIndex) -> None:
        self.index = index

    async def retention_report(self, *, today: date | None = None) -> list[dict]:
        """Per-chunk retention stats, oldest first — dashboard feed."""
        rows = await self.index.list_chunks()
        out = []
        for r in rows:
            age = max(timedelta(0), (today or date.today()) - r["observed_at"]).days
            if r["evergreen"]:
                retention = 1.0
            else:
                retention = retention_fraction(age)
            out.append(
                {
                    "path": r["path"],
                    "chunk_index": r["chunk_index"],
                    "content": r["content"][:80],
                    "observed_at": r["observed_at"].isoformat(),
                    "age_days": age,
                    "retention": round(retention, 4),
                    "evergreen": r["evergreen"],
                }
            )
        return out

    async def rot_report(self, *, threshold: float | None = None, today: date | None = None) -> list[RotEntry]:
        """Memories that have decayed past usefulness AND were never
        reinforced. Flagged in dreams; the owner decides (`/forget`)."""
        gate = threshold or settings.rot_threshold
        today = today or date.today()
        out: list[RotEntry] = []
        for r in await self.index.list_chunks():
            if r["evergreen"]:
                continue
            age = (today - r["observed_at"]).days
            retention = retention_fraction(age)
            if retention < gate:
                reason = f"retention {retention:.2f} below gate {gate:.2f} after {age}d"
                out.append(
                    RotEntry(
                        path=r["path"],
                        chunk_index=r["chunk_index"],
                        content=r["content"][:120],
                        retention=retention,
                        age_days=age,
                        reason=reason,
                    )
                )
        return sorted(out, key=lambda e: e.retention)

def supersede_in_text(content: str, target: str, marker: str) -> str | None:
    """Retire one entry in a curated file by appending a supersession marker.

    One implementation, shared by the `forget` tool and the HITL
    `/forget/confirm` endpoint, which had drifted apart. Indexed chunks may
    carry a contextual-retrieval header prepended at index time, so an exact
    replace of `target` against the raw file can miss even when the fact is
    present — fall back to locating the line that contains the hit's probe
    text. Returns None when the entry cannot be located, so the caller can
    refuse rather than retire the wrong line.

    Deliberately no looser fuzzy matching: the owner approves a specific hit,
    and a looser match could supersede a different line than the one shown.
    """
    new = content.replace(target, f"{target} {marker}")
    if new != content:
        return new
    # Walk the target's own lines, longest first: the longest line is the most
    # specific, so it identifies the intended entry with the least chance of
    # matching a neighbour.
    probes = [line.strip() for line in str(target).splitlines() if line.strip()]
    for probe_line in sorted(probes, key=len, reverse=True):
        probe = probe_line[:120]
        target_line = next((line for line in content.splitlines() if probe in line), None)
        if target_line is None:
            continue
        new = content.replace(target_line, f"{target_line} {marker}")
        if new != content:
            return new
    return None


def supersession_stats(memory_md: str) -> dict:
    """Count retired entries in a curated file — for the dashboard."""
    superseded = sum(1 for line in memory_md.splitlines() if "(superseded" in line)
    total = sum(1 for line in memory_md.splitlines() if line.strip().startswith("- ["))
    return {"entries": total, "superseded": superseded}


def age_distribution(rows: list[dict], *, today: date | None = None) -> list[tuple[str, int]]:
    """Bucket chunks by age for the memory heatmap: [("0-7d", n), ...]."""
    today = today or date.today()
    buckets: dict[str, int] = {}
    for r in rows:
        age = (today - r["observed_at"]).days
        key = (
            "0-7d" if age <= 7
            else "8-30d" if age <= 30
            else "31-90d" if age <= 90
            else "90d+"
        )
        buckets[key] = buckets.get(key, 0) + 1
    order = ["0-7d", "8-30d", "31-90d", "90d+"]
    return [(k, buckets.get(k, 0)) for k in order]
