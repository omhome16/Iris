"""No token, no socket, and connect refuses."""


def test_component():
    import asyncio
    import socket
    from pathlib import Path

    from iris_ai.plug import check_folder, construct, load_class

    folder = Path(__file__).parent
    ok, detail = check_folder(folder)
    assert ok, detail
    channel = construct(load_class(folder), None, trust="local")

    def _refuse(*args, **kwargs):
        raise AssertionError("telegram-local opened a socket")

    original = socket.socket
    socket.socket = _refuse  # type: ignore[assignment]
    try:
        async def run() -> None:
            assert await channel.connect() is False
            assert await channel.send_message(1, "hi") == "telegram-local:not-delivered"

        asyncio.run(run())
    finally:
        socket.socket = original
