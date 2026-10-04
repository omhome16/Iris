"""Catalog components, the channel runner's neighbors, evolve, and plugins."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iris_ai.catalog.capture.decision_only import DecisionOnly, keep
from iris_ai.catalog.consolidator.conflict_resolver import ConflictResolver
from iris_ai.catalog.persona.strict_reviewer import StrictReviewer
from iris_ai.engine import _origin
from iris_ai.engines.plan_execute import EngineEvent, PlanExecute
from iris_ai.evolve.frontier import leaks, pareto
from iris_ai.kernel.runner import EngineRunner
from iris_ai.plugins_interop import export_component, load_plugin
from iris_ai.sdk.engine import FINISH
from iris_ai.sdk.types import ConsolidationRequest, MemoryCandidate


def test_decision_only_keeps_a_decision_and_drops_a_question():
    assert keep("From now on I use uv.")
    assert not keep("What is the weather?")
    assert not keep("I am thinking about Rust someday.")


@pytest.mark.asyncio
async def test_decision_only_returns_a_candidate():
    found = await DecisionOnly().extract(type("R", (), {"user_message": "We decided to use uv.", "reply": "ok"})())
    assert found and found[0].kind == "decision"


def test_strict_reviewer_starts_with_a_verdict_rule():
    text = StrictReviewer().text()
    assert text.startswith("Review")
    assert "Verdict:" in text
    assert "praise" in text


@pytest.mark.asyncio
async def test_a_contradiction_becomes_a_conflict_not_a_second_fact():
    request = ConsolidationRequest(
        notes="I have moved to Rust for AI work.",
        curated="- [6] I prefer Python for AI work.",
        prior=type("P", (), {"add": (MemoryCandidate(content="I have moved to Rust for AI work.", kind="preference"),)})(),
    )
    plan = await ConflictResolver().propose(request)
    assert plan.conflicts
    assert plan.add == ()


def test_pareto_drops_the_dominated_row_and_leakage_catches_a_copied_case():
    rows = [
        {"name": "a", "score": 0.9, "tokens": 100},
        {"name": "b", "score": 0.5, "tokens": 200},
        {"name": "c", "score": 0.9, "tokens": 80},
    ]
    names = {row["name"] for row in pareto(rows)}
    assert "b" not in names
    assert "c" in names
    assert leaks("the answer is I prefer Python for AI work", ["I prefer Python for AI work"]) == [
        "I prefer Python for AI work"
    ]


@pytest.mark.asyncio
async def test_plan_execute_finishes_inside_the_call_cap():
    engine = PlanExecute(max_model_calls=6)
    runner = EngineRunner(engine, type("S", (), {"model": type("M", (), {"issued": set()})()})())
    step = await runner.run_node("plan", {})
    assert step.next == "act"
    done = await runner.run_node("verify", {"engine": {"steps": ["a"]}})
    assert done.next == FINISH
    assert EngineEvent("plan-execute", "verify").render() == "plan-execute: verify"


def test_plugin_json_reports_unknown_fields_and_export_round_trips(tmp_path: Path):
    root = tmp_path / "plugin"
    skill = root / "skills" / "notes"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# notes\n", encoding="utf-8")
    (root / "plugin.json").write_text(
        json.dumps({"name": "notes", "version": "0.1.0", "extra": "ignored"}),
        encoding="utf-8",
    )
    loaded = load_plugin(root)
    assert loaded["unknown"] == ["extra"]
    assert loaded["skills"]
    source = tmp_path / "component.py"
    source.write_text("class C:\n    pass\n", encoding="utf-8")
    exported = export_component("context", "temporal-rag", source, tmp_path / "out")
    again = load_plugin(exported)
    assert again["name"] == "iris-context-temporal-rag"
    assert again["components"]


def test_evidence_memory_ranks_a_current_fact_first():
    from iris_ai.catalog.memory.evidence import current_first

    class Hit:
        def __init__(self, content: str, score: float) -> None:
            self.content = content
            self.score = score

    ordered = current_first(
        [
            Hit("I prefer Python. (superseded 2026-06-01)", 0.9),
            Hit("I have moved to Rust for AI work.", 0.4),
        ]
    )
    assert ordered[0].content.startswith("I have moved")


def test_a_requirement_compares_against_this_install():
    from iris_ai.requires import accepts

    assert accepts("iris>=0.4,<1.0", "0.4.0")
    assert not accepts("iris>=0.5", "0.4.0")


def test_origin_rejects_an_unknown_value():
    assert _origin("untrusted") == "untrusted"
    with pytest.raises(ValueError):
        _origin("stranger")
