"""One critique, then one retry. The draft is not the answer that is returned."""


def test_component():
    import asyncio
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    engine = construct(load_class(folder), None, trust="local")

    class Model:
        def __init__(self) -> None:
            self.calls = []

        async def complete(self, prompt: str, **kwargs):
            del kwargs
            self.calls.append(prompt)
            if prompt.lower().startswith("critique"):
                return "Name the source."
            if prompt.lower().startswith("revise"):
                return "Found the note."
            return "Looking."

    async def run() -> None:
        model = Model()
        text = await engine.respond("Find the note.", model)
        assert text == "Found the note."
        assert engine.retries == 1
        assert len(model.calls) == 3
        assert engine.wants_plan({}) is False

    asyncio.run(run())
