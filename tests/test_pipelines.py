"""Pipelines fold. A cycle refuses. A budget drops the highest priority first."""

from __future__ import annotations

import pytest

from iris_ai.pipeline import expand_extends, parse_names, redact_candidates, selection, trim_blocks
from iris_ai.sdk.types import ContextBlock, MemoryCandidate


def test_a_comma_list_and_a_table_are_the_same_pipeline():
    assert parse_names("default, temporal-rag") == ["default", "temporal-rag"]
    section = {"context": {"pipeline": ["default", "budget"]}}
    assert selection(section, "context", default="default") == ["default", "budget"]


def test_extends_expands_and_a_cycle_refuses():
    assert expand_extends(["temporal-rag"], {"temporal-rag": "default"}) == ["default", "temporal-rag"]
    with pytest.raises(ValueError, match="cycle"):
        expand_extends(["a"], {"a": "b", "b": "a"})


def test_budget_drops_the_highest_priority_number_first():
    blocks = [
        ContextBlock(title="keep", text="short", priority=0),
        ContextBlock(title="drop", text="x" * 50, priority=90),
    ]
    kept = trim_blocks(blocks, max_chars=20)
    assert [block.title for block in kept] == ["keep"]


def test_redact_drops_a_candidate_that_would_be_scrubbed():
    secret = MemoryCandidate(content="api key sk-1234567890abcdefghij")
    plain = MemoryCandidate(content="Owner prefers uv.")
    kept = redact_candidates([secret, plain])
    assert kept == [plain]
