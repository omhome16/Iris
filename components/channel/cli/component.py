"""CLI channel. The outbox is the terminal. Nothing is opened on the network."""

from __future__ import annotations


class CliChannel:
    name = "cli"

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options
        self.connected = False
        self.outbox: list[tuple[int, str]] = []

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def close(self) -> None:
        self.connected = False

    async def send_message(self, chat_id: int, text: str) -> str:
        self.outbox.append((int(chat_id), text))
        return f"cli:{len(self.outbox)}"

    async def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> str:
        del photo_url
        return await self.send_message(chat_id, caption or "photo")

    async def get_chat_history(self, chat_id: int, limit: int = 10) -> str:
        rows = [text for cid, text in self.outbox if cid == int(chat_id)]
        return "\n".join(rows[-limit:])
