"""A Telegram-shaped channel that never dials.

There is no bot token in this component and no URL to call. `connect`
returns False. `send_message` returns a receipt that says the message was
not delivered. The real Telegram transport stays the registered `telegram`
channel, which does need a token and a server.
"""

from __future__ import annotations


class TelegramLocal:
    name = "telegram-local"

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options
        self.connected = False
        self.queued: list[tuple[int, str]] = []

    async def connect(self) -> bool:
        self.connected = False
        return False

    async def close(self) -> None:
        self.connected = False

    async def send_message(self, chat_id: int, text: str) -> str:
        self.queued.append((int(chat_id), text))
        return "telegram-local:not-delivered"

    async def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> str:
        del photo_url
        return await self.send_message(chat_id, caption or "photo")

    async def get_chat_history(self, chat_id: int, limit: int = 10) -> str:
        rows = [text for cid, text in self.queued if cid == int(chat_id)]
        return "\n".join(rows[-limit:])
