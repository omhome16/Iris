"""The cross-component matrix.

One cell is one memory, context, engine, capture, and consolidator. A cell is
broken when any part cannot round-trip a memory write. `iris components matrix
--dry-run` prints the grid and runs nothing.
"""

from __future__ import annotations

from itertools import product

ORDER = ("memory", "context", "engine", "capture", "consolidator")

# markdown writes are no-ops, so a cell that uses it cannot round-trip.
CATALOG: dict[str, tuple[tuple[str, str], ...]] = {
    "memory": (
        ("sqlite", "known-good"),
        ("evidence-memory", "known-good"),
        ("recency", "known-good"),
        ("graph", "known-good"),
        ("markdown", "broken"),
        ("pgvector", "broken"),
    ),
    "context": (
        ("default", "known-good"),
        ("minimal", "known-good"),
        ("temporal-rag", "known-good"),
    ),
    "engine": (
        ("react", "known-good"),
        ("plan-execute", "known-good"),
    ),
    "capture": (
        ("default", "known-good"),
        ("decision-only", "known-good"),
    ),
    "consolidator": (
        ("dreaming", "known-good"),
        ("off", "known-good"),
    ),
}


def cells() -> list[dict[str, str]]:
    """Every combination, tagged known-good or broken."""
    rows: list[dict[str, str]] = []
    axes = [CATALOG[kind] for kind in ORDER]
    for combo in product(*axes):
        spec = {kind: name for kind, (name, _status) in zip(ORDER, combo, strict=True)}
        spec["status"] = "broken" if any(status == "broken" for _name, status in combo) else "known-good"
        rows.append(spec)
    return rows


def format_dry_run() -> str:
    lines = []
    for cell in cells():
        parts = " ".join(f"{kind}={cell[kind]}" for kind in ORDER)
        lines.append(f"{cell['status']} {parts}")
    return "\n".join(lines)


def run_matrix() -> dict[str, object]:
    """Run every known-good cell. Broken cells are listed and not booted."""
    from iris_ai.bench.harness import drive_cell

    results: list[dict[str, object]] = []
    for cell in cells():
        if cell["status"] == "broken":
            results.append({**cell, "ran": False, "ok": False, "problems": ["broken"]})
            continue
        outcome = drive_cell(cell)
        results.append({**cell, "ran": True, **outcome})
    failed = [row for row in results if row.get("ran") and not row.get("ok")]
    return {"results": results, "failed": failed, "count": len(results)}
