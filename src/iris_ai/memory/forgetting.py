"""Forgetting — decay curves, supersession and rot detection.

Ebbinghaus-style exponential decay for episodic content (already used at
recall time in `index.recency_weight`); here we expose the *curves* for API
consumers and flag memory rot: entries past their useful half-life that have
been superseded or never reinforced.

Forgetting is a first-class function: measurable, trace-visible,
manually overridable (`/forget`). Nothing is hard-deleted silently — retired
entries carry `(superseded <date>)` markers instead.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from iris_ai.config import settings
from iris_ai.memory.index import MemoryIndex


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
    """Retention series: (day, retention) pairs."""
    return [(d, round(retention_fraction(d, half_life_days=half_life_days), 4)) for d in range(span_days + 1)]


class ForgettingEngine:
    """Detects rot and computes retention stats over the index."""

    def __init__(self, index: MemoryIndex) -> None:
        self.index = index

    async def retention_report(self, *, today: date | None = None) -> list[dict]:
        """Per-chunk retention stats, oldest first — API feed."""
        rows = await self.index.list_chunks()
        out = []
        for r in rows:
            observed = as_date(r["observed_at"])
            age = max(timedelta(0), (today or date.today()) - observed).days
            if r["evergreen"]:
                retention = 1.0
            else:
                retention = retention_fraction(age)
            out.append(
                {
                    "path": r["path"],
                    "chunk_index": r["chunk_index"],
                    "content": r["content"][:80],
                    "observed_at": observed.isoformat(),
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
            age = (today - as_date(r["observed_at"])).days
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

def as_date(value: object) -> date:
    """A date from a date, a datetime, or the ISO text SQLite stores."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _collapse(text: str) -> str:
    """Whitespace-collapsed text, for comparing a chunk against the file it came
    from: chunking flattens a chunk's line breaks into single spaces."""
    return " ".join(text.split())


_TOKEN = re.compile(r"[a-z0-9']+")
_STOP = {"the", "and", "for", "with", "from", "that", "this", "owner", "note"}

# Exclusive slots: a new fact with the same subject retires the older line.
# Additive facts (allergies, preferences) are not in this list.
# Location covers first person and the short forms people actually say
# ("I now live in Bengaluru", "I moved to", "My home is in").
_SLOTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "location",
        re.compile(
            r"^(?P<subj>.+?)\s+(?:(?:now|currently|still)\s+)?"
            r"(?:(?:live|lives|lived)\s+in|(?:move|moved|moves)\s+to|"
            r"(?:reside|resides|resided)\s+in|(?:am|is|are)\s+(?:based|located)\s+in|"
            r"home\s+is\s+in)\s+(?P<obj>.+)$",
            re.IGNORECASE,
        ),
    ),
    (
        "project",
        re.compile(
            r"^(?P<subj>.+?)\s+main project is(?: called)?\s+(?P<obj>.+)$",
            re.IGNORECASE,
        ),
    ),
)

# First person, "the user", and "the owner" are the same person in curated memory.
_OWNER_SUBJECTS = frozenset({"i", "im", "i'm", "me", "my", "we", "our", "owner", "user"})


def _tokens(text: str) -> set[str]:
    return {token for token in _TOKEN.findall(text.casefold()) if len(token) > 2 and token not in _STOP}


def _overlap(left: str, right: str) -> int:
    return len(_tokens(left) & _tokens(right))


def _fact_body(line: str) -> str:
    """The fact itself, without the bullet, score, triggers or stamps."""
    text = line.strip()
    text = re.sub(r"^-\s*", "", text)
    text = re.sub(r"^\[\d+(?:\.\d+)?\]\s*", "", text)
    text = re.sub(r"\s*\(triggers:.*?\)", "", text)
    text = re.sub(r"\s*\(by [^)]*\)", "", text)
    text = re.sub(r"\s*\(from:.*?\)", "", text)
    text = re.sub(r"\s*\(superseded [^)]*\)", "", text)
    text = re.sub(r"\s*\(note\)", "", text)
    return " ".join(text.split())


def _subject(text: str) -> str:
    folded = " ".join(text.casefold().split())
    if folded.startswith("the "):
        folded = folded[4:]
    if folded in _OWNER_SUBJECTS:
        return "owner"
    return folded


def _object(text: str) -> str:
    obj = " ".join(text.casefold().split())
    obj = re.sub(r"\s*\([^)]*\)\s*$", "", obj)
    return obj.rstrip(".,;")


def _slot(text: str) -> tuple[str, str, str] | None:
    body = _fact_body(text)
    for name, pattern in _SLOTS:
        match = pattern.match(body)
        if match is None:
            continue
        return name, _subject(match.group("subj")), _object(match.group("obj"))
    return None


def reconcile_contradictions(content: str, new_fact: str, marker: str) -> str:
    """Mark older exclusive-slot lines superseded when `new_fact` replaces them.

    "Lives in Pune" then "I now live in Bengaluru" retires the Pune line. A fact
    that is not one of those slots is left alone, and so is a line that was
    already superseded.
    """
    incoming = _slot(new_fact)
    if incoming is None or not content.strip():
        return content
    slot, subject, obj = incoming
    lines = content.splitlines()
    changed = False
    for index, line in enumerate(lines):
        if "(superseded" in line.casefold():
            continue
        existing = _slot(line)
        if existing is None:
            continue
        if existing[0] == slot and existing[1] == subject and existing[2] != obj:
            lines[index] = f"{line} {marker}"
            changed = True
    if not changed:
        return content
    text = "\n".join(lines)
    if content.endswith("\n"):
        text += "\n"
    return text


def locate_memory_line(content: str, target: str, *, query: str = "") -> str | None:
    """The one MEMORY.md line `target` refers to, or None when that is ambiguous.

    A small file is often one chunk, so every line is "contained" in the hit.
    Picking the longest line then retires the wrong fact. Several contained
    lines are resolved by token overlap with the owner's query; a tie refuses.
    """
    stripped = str(target).strip()
    if stripped:
        exact = [line for line in content.splitlines() if line.strip() == stripped]
        if len(exact) == 1:
            return exact[0]
        contained_probe = [line for line in content.splitlines() if stripped in line]
        if len(contained_probe) == 1:
            return contained_probe[0]
    probes = [
        line.strip()
        for line in str(target).splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    found: list[str] = []
    for probe in probes:
        probe = probe[:120]
        hits = [line for line in content.splitlines() if probe in line]
        if len(hits) == 1:
            found.append(hits[0])
    unique = list(dict.fromkeys(found))
    # One distinctive probe is enough. Several (a whole-file chunk) are not:
    # the longest of them is the bug this function exists to avoid.
    if len(unique) == 1:
        return unique[0]
    needle = _collapse(str(target))
    contained = [
        line
        for line in content.splitlines()
        if line.strip()
        and not line.lstrip().startswith("#")
        and _collapse(line) in needle
    ]
    if unique and not contained:
        contained = unique
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        hint = query.strip()
        if not hint:
            return None
        scored = sorted(((_overlap(line, hint), line) for line in contained), key=lambda item: item[0], reverse=True)
        best, runner = scored[0][0], scored[1][0]
        if best > 0 and best > runner:
            return scored[0][1]
        return None
    return None


def supersede_in_text(content: str, target: str, marker: str, *, query: str = "") -> str | None:
    """Retire one entry in a curated file by appending a supersession marker.

    One implementation, shared by the `forget` tool and the HITL
    `/forget/confirm` endpoint, which had drifted apart. Indexed chunks may
    carry a contextual-retrieval header prepended at index time, so an exact
    replace of `target` against the raw file can miss even when the fact is
    present. Returns None when the entry cannot be located uniquely, so the
    caller can refuse rather than retire the wrong line.
    """
    single = str(target)
    if single and single in content and "\n" not in single.strip("\n") and content.count(single) == 1:
        # The target is a single span already in the file (the exact-line case).
        new = content.replace(single, f"{single} {marker}", 1)
        if new != content:
            return new
    line = locate_memory_line(content, target, query=query)
    if line is None:
        return None
    return content.replace(line, f"{line} {marker}", 1)


def supersession_stats(memory_md: str) -> dict:
    """Count retired entries in a curated file — for API consumers."""
    superseded = sum(1 for line in memory_md.splitlines() if "(superseded" in line)
    total = sum(1 for line in memory_md.splitlines() if line.strip().startswith("- ["))
    return {"entries": total, "superseded": superseded}


def age_distribution(rows: list[dict], *, today: date | None = None) -> list[tuple[str, int]]:
    """Bucket chunks by age for the memory heatmap: [("0-7d", n), ...]."""
    today = today or date.today()
    buckets: dict[str, int] = {}
    for r in rows:
        age = (today - as_date(r["observed_at"])).days
        key = (
            "0-7d" if age <= 7
            else "8-30d" if age <= 30
            else "31-90d" if age <= 90
            else "90d+"
        )
        buckets[key] = buckets.get(key, 0) + 1
    order = ["0-7d", "8-30d", "31-90d", "90d+"]
    return [(k, buckets.get(k, 0)) for k in order]
