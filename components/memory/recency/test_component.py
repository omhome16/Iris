"""Recency keeps the newest keyword hit in front. The sandbox runs this file."""


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
                {"path": "notes.md", "chunk_index": 1, "content": "old tea is stale"},
                {"path": "notes.md", "chunk_index": 0, "content": "new tea is current"},
            ]
        )
        hits = await store.search("tea")
        assert hits[0]["content"] == "new tea is current"
        await store.close()

    asyncio.run(run())
