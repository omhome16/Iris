"""The component matrix lists every combination and runs the known-good cells."""

from __future__ import annotations

from iris_ai.components.matrix import cells, format_dry_run, run_matrix


def test_dry_run_prints_every_combination_and_runs_nothing() -> None:
    text = format_dry_run()
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1350
    assert len(cells()) == 1350
    assert sum(line.startswith("known-good ") for line in lines) == 900
    assert sum(line.startswith("broken ") for line in lines) == 450
    assert any("memory=markdown" in line and line.startswith("broken") for line in lines)
    assert any("memory=sqlite" in line and line.startswith("known-good") for line in lines)
    from iris_ai.cli.components_cmd import run

    assert run("matrix", dry_run=True) == 0


def test_a_known_good_cell_round_trips_without_fallback() -> None:
    from iris_ai.bench.harness import drive_cell

    cell = next(
        item
        for item in cells()
        if item["status"] == "known-good"
        and item["memory"] == "sqlite"
        and item["context"] == "temporal-rag"
        and item["engine"] == "plan-execute"
        and item["capture"] == "decision-only"
        and item["consolidator"] == "off"
    )
    result = drive_cell(cell)
    assert result["ok"], result
    assert result["roundtrip"] is True
    assert result["fallback"] is False
    assert result["reply"] == "noted"


def test_known_good_cells_pass() -> None:
    report = run_matrix()
    assert report["count"] == 1350
    assert report["failed"] == []
    ran = [row for row in report["results"] if row["ran"]]
    assert len(ran) == 900
    assert all(row["roundtrip"] and row["fallback"] is False for row in ran)
