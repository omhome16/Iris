"""One evolve run. The kernel scores. Nothing activates."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from iris_ai.eval.score import compare_suite, list_suites
from iris_ai.evolve.frontier import leaks, pareto


def audit_only(platform: str | None = None) -> bool:
    return not str(platform or sys.platform).startswith("linux")


def run_evolve(
    kind: str,
    suite: str,
    *,
    root: Path,
    proposer: Callable[[int], list[dict[str, str]]] | None = None,
    iterations: int = 2,
    budget_usd: float = 1.0,
    allow_audit: bool = False,
    platform: str | None = None,
    cost_per_iteration: float = 0.05,
) -> dict[str, Any]:
    """Archive a run. Held-out scores are written only after the loop stops."""
    if audit_only(platform) and not allow_audit:
        raise PermissionError(
            "evolve refuses to run where isolation is audit-only. "
            "Use WSL2, or pass --allow-audit-isolation."
        )
    paths = [path for path in list_suites(kind) if path.stem == suite or path.name == suite]
    if not paths:
        raise FileNotFoundError(f"no suite {kind}/{suite}")
    path = paths[0]
    loaded = json.loads(path.read_text(encoding="utf-8"))
    heldout_cases = [
        case for case in loaded.get("cases") or [] if case.get("id") in set((loaded.get("split") or {}).get("heldout") or [])
    ]
    heldout_text = "\n".join(str(case.get("input") or "") for case in heldout_cases)
    folder = Path(root) / "evolve" / f"{kind}-{suite}"
    folder.mkdir(parents=True, exist_ok=True)
    propose = proposer or (lambda _index: [])
    spent = 0.0
    candidates: list[dict[str, Any]] = []
    for index in range(iterations):
        if spent + cost_per_iteration > budget_usd:
            break
        spent += cost_per_iteration
        for item in propose(index):
            name = str(item.get("name") or f"candidate-{index}")
            source = str(item.get("source") or "")
            notes = str(item.get("notes") or "")
            cand_dir = folder / name
            cand_dir.mkdir(parents=True, exist_ok=True)
            (cand_dir / "component.py").write_text(source, encoding="utf-8")
            (cand_dir / "notes.md").write_text(notes, encoding="utf-8")
            search = compare_suite(path, name, against="default", split="search")
            (cand_dir / "search.json").write_text(json.dumps(search, indent=2), encoding="utf-8")
            candidates.append(
                {
                    "name": name,
                    "metric": search.get("candidate"),
                    "tokens": 0,
                    "search": search,
                    "source": source,
                }
            )
    frontier = pareto(
        [{"name": item["name"], "score": item["metric"] or 0, "tokens": item["tokens"]} for item in candidates]
    )
    heldout = []
    for item in candidates:
        row = compare_suite(path, item["name"], against="default", split="heldout")
        leaked = leaks(item["source"], [heldout_text] if heldout_text.strip() else [])
        heldout.append({"name": item["name"], "heldout": row, "leakage": leaked})
    report = {
        "kind": kind,
        "suite": suite,
        "spent_usd": spent,
        "frontier": frontier,
        "heldout": heldout,
        "activated": False,
    }
    (folder / "frontier.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
