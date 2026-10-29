"""The webhook channel queues a payload. It does not bind a port."""


def test_component():
    import asyncio
    import socket
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    channel = construct(load_class(folder), None, trust="local")
    before = socket.socket
    opened: list[str] = []

    def _refuse(*args, **kwargs):
        opened.append("socket")
        raise AssertionError("webhook channel opened a socket")

    socket.socket = _refuse  # type: ignore[assignment]
    try:
        async def run() -> None:
            assert await channel.connect() is True
            receipt = await channel.send_message(7, "ping")
            assert receipt.startswith("webhook:1:")
            assert channel.pending[0] == {"chat_id": 7, "text": "ping"}

        asyncio.run(run())
    finally:
        socket.socket = before
    assert opened == []
