"""Recency is the floor. The graph store walks one hop. They do not rank alike."""

from __future__ import annotations

import asyncio
from pathlib import Path

from iris_ai.components import list_options
from iris_ai.eval.score import compare_suite, list_suites
from iris_ai.plug import component_folder, construct, load_class
from iris_ai.testing import memory_conformance


def _load(kind: str, name: str):
    folder = component_folder(kind, name)
    assert folder is not None, name
    return construct(load_class(folder), None, trust="local")


def test_recency_and_graph_are_listed_and_pass_the_suite() -> None:
    names = list_options("memory")
    assert "recency" in names
    assert "graph" in names
    assert "sqlite" in names
    assert "pgvector" in names
    for name in ("recency", "graph"):
        problems = asyncio.run(memory_conformance(_load("memory", name)))
        assert problems == [], (name, problems)


def test_recency_beats_the_graph_on_stale_facts() -> None:
    """Recency puts the newest line first. The graph keeps keyword order.

    The paired interval excludes zero. Both are deterministic, so the noise
    floor on three repeats is zero and the effect sits above it.
    """
    path = next(item for item in list_suites("memory") if item.stem == "supersession")
    row = compare_suite(path, "recency", against="graph")
    low, high = row["ci"]
    assert row["baseline"] == 1.0
    assert row["candidate"] == 0.0
    assert high < 0.0
    assert low == high
    assert row["seed"] == 0
    assert row["noise_floor"] == 0.0
    assert abs(row["candidate"] - row["baseline"]) > row["noise_floor"]
    assert (path.parent / "supersession.json").is_file() or path.is_file()


def test_recency_passes_the_verification_door() -> None:
    from iris_ai.plug import shipped_folder, verification

    suite, verified_at = verification(shipped_folder("memory", "recency"))
    assert suite == "memory-behaviour"
    assert verified_at


def test_graph_folder_is_the_one_eval_executes() -> None:
    folder = component_folder("memory", "graph")
    assert folder == Path(folder)
    assert (folder / "component.py").is_file()
