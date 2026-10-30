"""Webhook channel. `send_message` builds the JSON body and keeps it.

Nothing listens. A test can read `pending` to see what would have been
posted. Binding a port would make the suite depend on a free address.
"""

from __future__ import annotations

import json


class WebhookChannel:
    name = "webhook"

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options
        self.connected = False
        self.pending: list[dict] = []

    async def connect(self) -> bool:
        self.connected = True
        return True

    async def close(self) -> None:
        self.connected = False

    async def send_message(self, chat_id: int, text: str) -> str:
        payload = {"chat_id": int(chat_id), "text": text}
        self.pending.append(payload)
        return f"webhook:{len(self.pending)}:{json.dumps(payload, separators=(',', ':'))}"

    async def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> str:
        payload = {"chat_id": int(chat_id), "photo_url": photo_url, "caption": caption}
        self.pending.append(payload)
        return f"webhook:{len(self.pending)}"

    async def get_chat_history(self, chat_id: int, limit: int = 10) -> str:
        rows = [item["text"] for item in self.pending if item.get("chat_id") == int(chat_id) and "text" in item]
        return "\n".join(rows[-limit:])
