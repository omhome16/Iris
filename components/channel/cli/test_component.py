"""The CLI channel records a message and does not open a socket."""


def test_component():
    import asyncio
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    channel = construct(load_class(folder), None, trust="local")

    async def run() -> None:
        assert await channel.connect() is True
        receipt = await channel.send_message(1, "hello")
        assert receipt == "cli:1"
        assert "hello" in await channel.get_chat_history(1)
        await channel.close()
        assert channel.connected is False

    asyncio.run(run())
