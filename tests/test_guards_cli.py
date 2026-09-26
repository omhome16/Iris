"""`iris guards` — the ceiling readout, offline.

The point of the command is that a limit is inspectable without reading a trace
file and without a running engine: policy from settings, today's counters from
`config/budget.json`. These tests pin both halves, and the honest gap (live
circuit state is per-run).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from typer.testing import CliRunner

from iris_ai.cli import guards as guards_mod
from iris_ai.cli.main import app
from iris_ai.config import settings

runner = CliRunner()


def _write_day_file(root: Path, *, counters: dict[str, int]) -> Path:
    path = root / "config" / "budget.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"date": date.today().isoformat(), "counters": counters}), encoding="utf-8"
    )
    return path


def test_the_snapshot_carries_the_declared_chain():
    data = guards_mod.snapshot()
    assert data["order"] == ["budget", "circuit", "spiral", "context", "record"]
    assert data["spiral_min_repeats"] >= 2
    assert 0 < data["spiral_jaccard"] <= 1
    assert data["budget"]["policy"]["version"]


def test_the_command_prints_the_chain_and_todays_spend(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    _write_day_file(tmp_path, counters={"input": 1234, "output": 66, "cached": 900})

    result = runner.invoke(app, ["guards"])

    assert result.exit_code == 0
    for expected in ("budget", "circuit", "spiral", "spend", "1,234", "no ceiling"):
        assert expected in result.stdout
    # The reserved bucket is declared in code but not shown as a metric.
    assert "tool_schema" not in result.stdout


def test_a_missing_day_file_reads_as_zero_spend_not_an_error(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    result = runner.invoke(app, ["guards"])
    assert result.exit_code == 0
    assert "0" in result.stdout


def test_the_json_form_is_machine_readable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    _write_day_file(tmp_path, counters={"output": 42})

    result = runner.invoke(app, ["guards", "--json"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["order"][0] == "budget"
    assert data["budget"]["counters"]["output"] == 42


def test_the_output_survives_a_windows_console_encoding(monkeypatch, tmp_path: Path):
    """A character outside cp1252 is not a cosmetic problem on Windows: rich
    falls back to the legacy console renderer and the command dies with a
    `UnicodeEncodeError` traceback instead of printing anything.

    `iris guards` shipped a `↑` that did exactly that, so the rule is asserted
    rather than remembered.
    """
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    result = runner.invoke(app, ["guards"])
    assert result.exit_code == 0
    result.stdout.encode("cp1252")  # raises if a character is not representable


def test_it_says_that_live_circuit_state_is_not_in_this_view(monkeypatch, tmp_path: Path):
    """Honest gap: an offline readout cannot know which circuits are open, and
    printing \"none\" would be a lie told confidently."""
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    result = runner.invoke(app, ["guards"])
    assert "GET /guards" in result.stdout
