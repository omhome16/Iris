"""Unit tests: dreaming phases (Light gate, REM fallback, Deep consolidation),
forgetting curves, and the skill library. No DB, no API key."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from iris.memory.dreaming import DreamEngine, LightPhase, RemPhase, StagedSignal
from iris.memory.forgetting import age_distribution, retention_fraction, supersession_stats
from iris.memory.files import WorkspaceFiles
from iris.memory.provenance import Origin, Provenance
from iris.memory.skills import Skill, SkillLibrary


def make_signal(
    content: str,
    *,
    origin: Origin = Origin.AGENT,
    importance: float = 6.0,
    occurrences: int = 1,
    target: str = "",
) -> StagedSignal:
    return StagedSignal(
        op="ADD",
        content=content,
        importance=importance,
        triggers=["alpha", "beta"],
        target=target,
        provenance=Provenance(origin=origin, source="tests"),
        occurrences=occurrences,
    )


def write_staging(files: WorkspaceFiles, lines: list[str]) -> None:
    path = files.staging_dir() / "staging-2026-08-15.jsonl"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ── Light phase ─────────────────────────────────────────────────────────────

def test_light_dedupes_by_content(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    write_staging(
        files,
        [
            '{"op":"ADD","content":"Loves Ethiopian coffee","importance":6.0,"triggers":["coffee"],"target":"","provenance":{"origin":"agent","source":"chat","observed_at":"2026-08-15","session_id":"s1"}}',
            '{"op":"ADD","content":"loves ethiopian coffee","importance":8.0,"triggers":["coffee"],"target":"","provenance":{"origin":"agent","source":"chat","observed_at":"2026-08-15","session_id":"s2"}}',
        ],
    )
    promoted, staged = LightPhase().run(files.staging_dir())
    assert staged == 1, "casefold duplicates should collapse"
    assert promoted[0].importance == 8.0, "highest importance wins"


def test_light_gates_by_origin_and_importance(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    write_staging(
        files,
        [
            # untrusted scraped content must never promote
            '{"op":"ADD","content":"Scraped fact from webpage","importance":9.0,"triggers":[],"target":"","provenance":{"origin":"untrusted","source":"scrape"}}',
            # agent low-importance trivia stays staged
            '{"op":"ADD","content":"Owner wore a blue shirt","importance":2.0,"triggers":[],"target":"","provenance":{"origin":"agent","source":"chat"}}',
            # agent high-importance promotes
            '{"op":"ADD","content":"Owner is allergic to peanuts","importance":9.0,"triggers":["allergy"],"target":"","provenance":{"origin":"agent","source":"chat"}}',
            # owner always promotes even at low importance
            '{"op":"ADD","content":"Owner explicitly wants to be called Captain","importance":2.0,"triggers":["captain"],"target":"","provenance":{"origin":"owner","source":"chat"}}',
        ],
    )
    promoted, _ = LightPhase().run(files.staging_dir())
    contents = {p.content for p in promoted}
    assert "Scraped fact from webpage" not in contents
    assert "Owner wore a blue shirt" not in contents
    assert "Owner is allergic to peanuts" in contents
    assert "Owner explicitly wants to be called Captain" in contents


# ── REM phase ───────────────────────────────────────────────────────────────

class JunkLLM:
    async def complete(self, messages, **kwargs):
        return "not json at all"


class ThemeLLM:
    async def complete(self, messages, **kwargs):
        return '{"themes":[{"theme":"identity","statement":"Owner is Omar, an ML engineer","evidence":[0,1]}]}'


async def test_rem_falls_back_on_bad_json():
    signals = [make_signal("Owner is Omar")]
    themes = await RemPhase(JunkLLM()).run(signals)
    assert len(themes) == 1
    assert themes[0].statement == "Owner is Omar"


async def test_rem_groups_into_themes():
    signals = [
        make_signal("Owner is Omar", importance=8.0),
        make_signal("Owner works on memory engines", importance=7.0),
    ]
    themes = await RemPhase(ThemeLLM()).run(signals)
    assert len(themes) == 1
    assert themes[0].statement == "Owner is Omar, an ML engineer"
    assert themes[0].importance == 8.0  # max evidence importance


# ── Deep phase ──────────────────────────────────────────────────────────────

async def test_deep_consolidates_memory_md(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    files.write_curated(
        files.memory,
        "# MEMORY.md — Iris long-term memory\n\n- [7] Owner loves hiking (by owner, 2026-01-01)\n",
    )
    write_staging(
        files,
        [
            '{"op":"ADD","content":"Owner is Omar, an ML engineer","importance":8.0,"triggers":["omar"],"target":"","provenance":{"origin":"agent","source":"chat"}}',
        ],
    )
    engine = DreamEngine(llm=JunkLLM(), files=files)
    record = await engine.sleep()
    assert record.added >= 1
    text = files.memory.read_text(encoding="utf-8")
    assert "- [8]" in text, "consolidated statement should be appended"
    assert "(from: memory/2026-08-15.md)" in text


async def test_deep_supersedes_target_entry(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    files.write_curated(
        files.memory,
        "# MEMORY.md — Iris long-term memory\n\n- [7] Owner drinks green tea\n",
    )
    write_staging(
        files,
        [
            '{"op":"UPDATE","content":"Owner now drinks only herbal tea","importance":6.0,"triggers":["tea"],"target":"green tea","provenance":{"origin":"agent","source":"chat"}}',
        ],
    )
    await DreamEngine(llm=JunkLLM(), files=files).sleep()
    text = files.memory.read_text(encoding="utf-8")
    assert "(superseded" in text, "old entry should be retired, not deleted"
    assert "green tea" in text, "retired entry keeps its marker (no silent delete)"


async def test_deep_stores_preimage_and_dream_record(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    files.write_curated(files.memory, "# MEMORY.md — Iris long-term memory\n\n")
    write_staging(
        files,
        [
            '{"op":"ADD","content":"Owner plans a trip to Kyoto","importance":7.0,"triggers":["kyoto"],"target":"","provenance":{"origin":"agent","source":"chat"}}',
        ],
    )
    await DreamEngine(llm=JunkLLM(), files=files).sleep()
    pre = list((tmp_path / ".dreams" / "preimages").glob("*.md"))
    assert pre, "pre-image must be archived"
    dreams = files.dreams.read_text(encoding="utf-8")
    assert "## Dream" in dreams
    assert "added=" in dreams


# ── Forgetting ──────────────────────────────────────────────────────────────

def test_retention_fraction_half_life():
    assert retention_fraction(0) == 1.0
    assert round(retention_fraction(30), 3) == 0.5  # half-life


def test_supersession_stats():
    md = "# MEMORY.md\n\n- [7] A\n- [5] B (superseded 2026-01-01)\n- [3] C\n"
    stats = supersession_stats(md)
    assert stats["entries"] == 3
    assert stats["superseded"] == 1


def test_age_distribution_buckets():
    today = date(2026, 8, 15)
    rows = [
        {"observed_at": today - timedelta(days=3)},
        {"observed_at": today - timedelta(days=20)},
        {"observed_at": today - timedelta(days=60)},
        {"observed_at": today - timedelta(days=200)},
    ]
    buckets = dict(age_distribution(rows, today=today))
    assert buckets == {"0-7d": 1, "8-30d": 1, "31-90d": 1, "90d+": 1}


# ── Skills ──────────────────────────────────────────────────────────────────

def test_skill_library_roundtrip(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    lib = SkillLibrary(files)
    skill = Skill(
        name="Draft Standup",
        description="Write a concise standup update",
        triggers=["standup", "daily update"],
        procedure="1. List yesterday\n2. List today\n3. List blockers",
    )
    lib.write(skill)
    got = lib.get("Draft Standup")
    assert got is not None
    assert got.procedure.startswith("1. List")
    assert lib.list()[0].name == "Draft Standup"
    assert "Draft Standup" in [s.name for s in lib.match_triggers("my standup tomorrow")]


def test_skill_reinforce_and_delete(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    lib = SkillLibrary(files)
    lib.write(Skill(name="Focus Timer", description="Pomodoro helper", triggers=["pomodoro"]))
    lib.reinforce("Focus Timer", delta=0.2)
    assert lib.get("Focus Timer").success_score == 0.7
    assert lib.delete("Focus Timer") is True
    assert lib.get("Focus Timer") is None