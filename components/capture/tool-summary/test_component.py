"""A decision with no tool call is dropped. A tool line becomes one summary."""


def test_component():
    import asyncio
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class
    from iris_ai.sdk.types import CaptureRequest

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    component = construct(load_class(folder), None, trust="local")

    async def run() -> None:
        decision = CaptureRequest(user_message="We decided to use uv.", reply="Noted.")
        assert await component.extract(decision) == []
        call = CaptureRequest(user_message="look it up", reply="tool memory_search: found the note")
        found = await component.extract(call)
        assert len(found) == 1
        assert "memory_search" in found[0].content
        assert "found the note" in found[0].content

    asyncio.run(run())
