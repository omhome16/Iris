"""Telegram channel — iris-core connects to the telegram-mcp MCP server.

The bridge is a separate service (mcp_servers/telegram/) that wraps the
Telegram Bot API as an MCP server. Iris-core connects as an MCP *client*
over streamable HTTP, so the agent can use `send_message` / `get_chat_history`
as ordinary tools: Telegram becomes one more channel Iris can reach through
the same tool protocol.

Boot-tolerant: if the bridge is down, the channel is simply unavailable —
Iris itself keeps working. All methods here are no-ops via `unavailable`
rather than raising through the agent loop.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field

from mcp.client.client import Client

log = logging.getLogger("iris.telegram")


@dataclass(slots=True)
class TelegramMCPClient:
    """Thin MCP client wrapper. Connects lazily; callers use `connected`."""

    url: str
    _client: Client | None = field(default=None, init=False)

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def connect(self) -> bool:
        """Open the MCP session and advertise the server's tools. Idempotent."""
        if self._client is not None:
            return True
        try:
            client = Client(self.url)
            await client.__aenter__()
            await client.list_tools()
            self._client = client
            log.info("telegram MCP connected: %s", self.url)
            return True
        except (Exception, BaseExceptionGroup) as exc:
            # anyio raises BaseExceptionGroup (not Exception) on transport
            # failures; boot must survive a down bridge.
            log.warning("telegram MCP unavailable (%s): %s", self.url, exc, exc_info=True)
            return False

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception, BaseExceptionGroup):  # shutdown must not raise
                await self._client.__aexit__(None, None, None)
            self._client = None

    async def send_message(self, chat_id: int, text: str) -> str:
        if not self.connected:
            return "telegram channel unavailable"
        result = await self._client.call_tool("send_message", {"chat_id": chat_id, "text": text})
        return _text(result)

    async def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> str:
        if not self.connected:
            return "telegram channel unavailable"
        result = await self._client.call_tool(
            "send_photo", {"chat_id": chat_id, "photo_url": photo_url, "caption": caption}
        )
        return _text(result)

    async def get_chat_history(self, chat_id: int, limit: int = 10) -> str:
        if not self.connected:
            return "telegram channel unavailable"
        result = await self._client.call_tool(
            "get_chat_history", {"chat_id": chat_id, "limit": limit}
        )
        return _text(result)


def _text(result) -> str:
    if hasattr(result, "content"):
        return "\n".join(getattr(c, "text", str(c)) for c in result.content)
    return str(result)
