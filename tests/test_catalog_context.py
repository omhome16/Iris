"""Recall-first spends the budget on memory. Compaction-first keeps only the tail."""

from __future__ import annotations

import asyncio

from iris_ai.components import list_options
from iris_ai.eval.score import compare_suite, list_suites
from iris_ai.plug import component_folder, load_class
from iris_ai.sdk.context import ComponentContext
from iris_ai.sdk.types import ContextRequest
from iris_ai.testing import context_conformance


def test_both_contexts_are_listed_and_pass_the_suite() -> None:
    names = list_options("context")
    assert "recall-first" in names
    assert "compaction-first" in names
    for name in ("recall-first", "compaction-first"):
        folder = component_folder("context", name)
        assert folder is not None
        component = load_class(folder)(ComponentContext(api_version="iris/v1", kind="context", name=name))
        problems = asyncio.run(context_conformance(component))
        assert problems == [], (name, problems)


def test_compaction_and_recall_differ_on_stale_facts() -> None:
    """Compaction hides the old line in history. Recall shows it as current.

    Default and recall-first do not differ on stale_as_current: both surface
    the stale sentence as a current block. The difference is the budget.
    """
    path = next(item for item in list_suites("context") if item.stem == "temporal-recall")
    differed = compare_suite(path, "compaction-first", against="recall-first")
    low, high = differed["ci"]
    assert differed["baseline"] == 1.0
    assert differed["candidate"] == 0.0
    assert low < 0.0 and high < 0.0
    assert differed["noise_floor"] == 0.0
    tied = compare_suite(path, "recall-first", against="default")
    tie_low, tie_high = tied["ci"]
    assert tied["baseline"] == tied["candidate"] == 1.0
    assert tie_low <= 0.0 <= tie_high

    folder = component_folder("context", "recall-first")
    component = load_class(folder)(
        ComponentContext(api_version="iris/v1", kind="context", name="recall-first")
    )

    class _Index:
        async def search(self, query: str, *, top_k: int = 5):
            del query, top_k

            class _Hit:
                content = "a recalled fact"
                path = "MEMORY.md"

            return [_Hit()]

    from iris_ai.sdk.context import MemoryAccess

    component.ctx = ComponentContext(
        api_version="iris/v1", kind="context", name="recall-first", memory=MemoryAccess(_Index())
    )

    async def _budget():
        result = await component.assemble(ContextRequest(message="fact", session_id="t"))
        from iris_ai.pipeline import trim_blocks

        kept = trim_blocks(list(result.blocks), max_chars=20)
        assert any(block.kind == "memory" for block in kept)
        assert all(block.kind != "note" for block in kept)

    asyncio.run(_budget())
