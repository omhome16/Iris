"""Overlapping lines are one sentence. The plan does not retire the old line."""


def test_component():
    import asyncio
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class
    from iris_ai.sdk.types import ConsolidationRequest

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    component = construct(load_class(folder), None, trust="local")

    async def run() -> None:
        plan = await component.propose(
            ConsolidationRequest(
                notes="I have moved to Rust for AI work.",
                curated="- [6] I prefer Python for AI work.",
            )
        )
        assert plan.supersede == ()
        assert plan.conflicts == ()
        assert "Python" in plan.add[0].content
        assert "Rust" in plan.add[0].content

    asyncio.run(run())
