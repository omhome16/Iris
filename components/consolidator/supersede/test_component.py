"""A moved preference retires the old line. The plan does not merge the two sentences."""


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
        assert plan.conflicts == ()
        assert plan.supersede
        assert "Python" in plan.supersede[0]
        assert plan.add[0].content == "I have moved to Rust for AI work."
        assert "Python" not in plan.add[0].content

    asyncio.run(run())
