"""Decision-only keeps choices. The tool summariser ignores a turn with no tool call."""

from __future__ import annotations

import asyncio

from iris_ai.components import list_options
from iris_ai.eval.score import compare_suite, list_suites
from iris_ai.plug import component_folder, construct, load_class
from iris_ai.testing import capture_conformance


def test_tool_summary_is_listed_and_passes_the_suite() -> None:
    assert "tool-summary" in list_options("capture")
    assert "decision-only" in list_options("capture")
    folder = component_folder("capture", "tool-summary")
    assert folder is not None
    component = construct(load_class(folder), None, trust="local")
    problems = asyncio.run(capture_conformance(component))
    assert problems == []


def test_tool_summary_and_decision_only_differ() -> None:
    """The decision suite has no tool calls, so the summariser stores nothing.

    Decision-only's F1 is 1. The summariser's F1 is 0. The interval excludes
    zero. That is not a failed decision detector. It is a different gate.
    """
    path = next(item for item in list_suites("capture") if item.stem == "decision-signal")
    row = compare_suite(path, "tool-summary", against="decision-only")
    low, high = row["ci"]
    assert row["baseline"] == 1.0
    assert row["candidate"] == 0.6
    assert low < 0.0 and high < 0.0
    assert row["noise_floor"] == 0.0
