"""Recalled lines sit at priority 0. A scratch note is the one a budget drops."""


def test_component():
    import asyncio
    from pathlib import Path

    from iris_ai.plug import check_folder, load_class
    from iris_ai.sdk.context import ComponentContext, MemoryAccess

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail

    class _Hit:
        def __init__(self, content: str) -> None:
            self.content = content
            self.path = "MEMORY.md"

    class _Index:
        async def search(self, query: str, *, top_k: int = 5):
            del query, top_k
            return [_Hit("I prefer Python for AI work."), _Hit("I have moved to Rust for AI work.")]

    ctx = ComponentContext(api_version="iris/v1", kind="context", name="recall-first", memory=MemoryAccess(_Index()))
    component = load_class(folder)(ctx)

    async def run():
        from iris_ai.sdk.types import ContextRequest

        result = await component.assemble(ContextRequest(message="AI work", session_id="t"))
        kinds = [block.kind for block in result.blocks if block.kind == "memory"]
        assert len(kinds) == 2
        assert result.blocks[0].priority == 0
        assert any(block.priority == 90 for block in result.blocks)

    asyncio.run(run())
