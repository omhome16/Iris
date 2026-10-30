"""Supersede retires a line. Merge-by-similarity keeps both texts in one sentence."""

from __future__ import annotations

import asyncio

from iris_ai.components import list_options
from iris_ai.eval.score import compare_suite, list_suites
from iris_ai.plug import component_folder, construct, load_class
from iris_ai.testing import consolidator_conformance


def test_both_consolidators_are_listed_and_pass_the_suite() -> None:
    names = list_options("consolidator")
    assert "supersede" in names
    assert "merge-similarity" in names
    assert "dreaming" in names
    for name in ("supersede", "merge-similarity"):
        folder = component_folder("consolidator", name)
        assert folder is not None
        component = construct(load_class(folder), None, trust="local")
        problems = asyncio.run(consolidator_conformance(component))
        assert problems == [], (name, problems)


def test_supersede_and_merge_differ_on_silent_overwrite() -> None:
    """Supersede names the old line, so the overwrite is not silent.

    Merge-by-similarity writes one combined sentence and does not retire the
    old line, so silent_overwrite stays 1. The interval excludes zero.
    Conflict-resolver and supersede do not differ on this metric: both refuse
    a quiet overwrite. One emits a conflict, the other a supersede list.
    """
    path = next(item for item in list_suites("consolidator") if item.stem == "conflicts")
    row = compare_suite(path, "supersede", against="merge-similarity")
    low, high = row["ci"]
    assert row["baseline"] == 1.0
    assert row["candidate"] == 0.0
    assert low < 0.0 and high < 0.0
    assert row["noise_floor"] == 0.0
    tied = compare_suite(path, "supersede", against="conflict-resolver")
    tie_low, tie_high = tied["ci"]
    assert tied["baseline"] == tied["candidate"] == 0.0
    assert tie_low <= 0.0 <= tie_high
