"""A keyword seed pulls in the chunk one typed edge away."""


def test_component():
    import asyncio
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    store = construct(load_class(folder), None, trust="local")

    async def run() -> None:
        await store.connect()
        await store.upsert_chunks(
            [
                {"path": "a.md", "chunk_index": 0, "content": "alpha tea"},
                {"path": "b.md", "chunk_index": 0, "content": "beta kettle"},
            ]
        )
        store.link("a.md", 0, "b.md", 0, "related")
        hits = await store.search("tea")
        contents = [hit["content"] for hit in hits]
        assert contents[0] == "alpha tea"
        assert "beta kettle" in contents
        assert hits[-1].get("via") == "related"
        await store.close()

    asyncio.run(run())
