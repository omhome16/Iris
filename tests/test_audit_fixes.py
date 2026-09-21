"""Regression tests for the audit fixes.

Each test pins a behaviour that was previously wrong or unenforced, so the fix
cannot silently regress: secret redaction, line-safe budget truncation, the
one-copy supersession helper, the daily-note writer guard in dreaming, and the
bare-"tomorrow" scheduling default.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from iris.config import Settings, settings
from iris.memory.dreaming import DreamEngine, LightPhase, StagedSignal
from iris.memory.files import WorkspaceFiles
from iris.memory.forgetting import supersede_in_text
from iris.memory.provenance import Origin, Provenance
from iris.tasks import DEFAULT_REMINDER_HOUR, parse_when

# ── secrets must never print ────────────────────────────────────────────────

def test_settings_repr_hides_every_secret(monkeypatch):
    """`repr(settings)` lands in logs and in pytest failure summaries."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "ts-super-secret-value")
    monkeypatch.setenv("IRIS_API_TOKEN", "bearer-super-secret")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:bot-secret")
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://u:pw-secret@h/db")

    rendered = repr(Settings(_env_file=None))

    for leaked in (
        "ts-super-secret-value",
        "bearer-super-secret",
        "123456:bot-secret",
        "tvly-secret",
        "pw-secret",
    ):
        assert leaked not in rendered, f"{leaked} leaked into Settings repr"
    # Non-secret config is still visible, so the repr stays useful for debugging.
    assert "strong_model" in rendered and "jev-latest" in rendered


# ── budget truncation must keep markdown structure ──────────────────────────

def test_budgeted_read_keeps_lines_not_a_wall_of_text(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "bootstrap_budget_tokens", 80)
    files = WorkspaceFiles(tmp_path)
    body = "# MEMORY.md\n\n" + "\n".join(f"- fact number {i} about the owner" for i in range(120))
    files.memory.write_text(body, encoding="utf-8")

    trimmed = files.bootstrap_memory()

    assert "truncated" in trimmed
    assert "\n- fact number" in trimmed, "bullets must survive truncation on their own lines"
    # The tail is what survives: curated files are append-mostly.
    assert "fact number 119" in trimmed
    assert "fact number 0 " not in trimmed


def test_budgeted_read_is_a_passthrough_under_budget(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    files.user.write_text("# USER.md\n\n- Name: Aria\n", encoding="utf-8")
    assert files.bootstrap_user() == "# USER.md\n\n- Name: Aria\n"


# ── one supersession implementation ────────────────────────────────────────

def test_supersede_in_text_exact_match():
    content = "- [5] Owner loves hiking (by owner, 2026-08-01)"
    marker = "(superseded 2026-09-21)"
    out = supersede_in_text(content, content, marker)

    assert out == f"{content} {marker}"


def test_supersede_in_text_falls_back_to_line_probe():
    """Indexed chunks can carry a contextual header, so an exact match misses."""
    raw = "# MEMORY.md\n- [5] Owner loves hiking\n"
    indexed = "Context about hobbies\n\n" + raw.splitlines()[1]
    out = supersede_in_text(raw, indexed, "(superseded 2026-09-21)")

    assert out is not None
    assert "(superseded" in out
    assert out.count("Owner loves hiking") == 1


def test_supersede_in_text_refuses_when_entry_is_missing():
    """Never retire the wrong line: an unreachable match must abort."""
    assert supersede_in_text("# MEMORY.md\n- something else\n", "not in the file at all", "(x)") is None


# ── dreaming must survive a corrupt staging line after promotion ────────────

def test_consume_survives_corrupt_staging_line(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    staging = files.staging_dir() / "staging-1.jsonl"
    good = json.dumps(
        {"op": "ADD", "content": "promoted fact", "importance": 9, "provenance": {"origin": "agent"}}
    )
    staging.write_text(good + "\n{ this is not json\n", encoding="utf-8")

    engine = DreamEngine(llm=None, files=files, index=None)  # type: ignore[arg-type]
    promoted = [
        StagedSignal(
            op="ADD",
            content="promoted fact",
            importance=9.0,
            triggers=[],
            target="",
            provenance=Provenance(origin=Origin.AGENT),
        )
    ]
    engine._consume(promoted)  # must not raise

    remaining = staging.read_text(encoding="utf-8")
    assert "promoted fact" not in remaining
    assert "not json" in remaining, "the corrupt line is kept, never silently dropped"


def test_light_phase_ignores_corrupt_staging_lines(tmp_path: Path):
    staging = tmp_path / "staging-1.jsonl"
    staging.write_text("garbage\n" + json.dumps({"content": "", "importance": 1}) + "\n", encoding="utf-8")
    promoted, staged = LightPhase().run(tmp_path)

    assert promoted == [] and staged == 0


# ── bare "tomorrow" schedules at the reminder hour ─────────────────────────

def test_bare_tomorrow_uses_the_reminder_hour(monkeypatch):
    monkeypatch.setattr(settings, "iris_timezone", "UTC")
    monkeypatch.setattr(settings, "nightly_sleep_hour", 4)

    when = parse_when("tomorrow")
    tz = ZoneInfo("UTC")

    assert when.hour == DEFAULT_REMINDER_HOUR, "a bare 'tomorrow' must not use the 04:00 dream hour"
    assert when.date() > datetime.now(tz).date()
    assert when.utcoffset() is not None


def test_parse_when_iso_and_relative_still_work(monkeypatch):
    monkeypatch.setattr(settings, "iris_timezone", "UTC")
    assert parse_when("2026-09-25T09:00").hour == 9
    later = parse_when("in 90 minutes")
    assert later > datetime.now(ZoneInfo("UTC")) + timedelta(minutes=80)


# ── the ledger must admit when a cost is not measured ──────────────────────

def test_ledger_flags_unpriced_models(tmp_path: Path):
    from iris.ledger import CostLedger

    ledger = CostLedger(tmp_path / "calls.jsonl")
    ledger.record(model="mystery/model-9000", tier="strong", prompt_tokens=10, completion_tokens=5)
    ledger.record(model="jev-latest", tier="jev", prompt_tokens=1_000_000, completion_tokens=0)

    totals = ledger.totals()

    assert totals["unpriced_models"] == ["mystery/model-9000"]
    assert totals["cost_is_measured"] is False, "a run containing an unknown model is not a measured cost"
    # $42/Btok = $0.042/Mtok input-only, so 1M input tokens is 4.2 cents.
    assert totals["cost"] == pytest.approx(0.042)


def test_ledger_marks_fully_priced_run_as_measured(tmp_path: Path):
    from iris.ledger import CostLedger

    ledger = CostLedger(tmp_path / "calls.jsonl")
    ledger.record(model="jev-latest", tier="jev", prompt_tokens=100, completion_tokens=0)

    totals = ledger.totals()

    assert totals["unpriced_models"] == []
    assert totals["cost_is_measured"] is True
