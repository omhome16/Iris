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

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field

try:  # Telegram rides the [mcp] extra
    from mcp.client.client import Client
except ImportError:  # pragma: no cover
    Client = None  # type: ignore[assignment,misc]

log = logging.getLogger("iris.telegram")


@dataclass(slots=True)
class TelegramMCPClient:
    """Thin MCP client wrapper. Connects lazily; callers use `connected`.

    This is the Telegram implementation of the `Channel` protocol
    (`iris_ai.channels.base`). It is one entry in the channel registry, not a
    special case in the boot path — a second channel is another registry entry.
    """

    url: str
    name: str = "telegram"
    timeout_s: float = 10.0
    _client: Client | None = field(default=None, init=False)

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def connect(self) -> bool:
        """Open the MCP session and advertise the server's tools. Idempotent.

        Bounded by `timeout_s` so a bridge that accepts a connection and then
        hangs cannot stall boot waiting on it.
        """
        if self._client is not None:
            return True
        try:
            client = Client(self.url)
            await asyncio.wait_for(client.__aenter__(), timeout=self.timeout_s)
            await asyncio.wait_for(client.list_tools(), timeout=self.timeout_s)
            self._client = client
            log.info("%s MCP connected: %s", self.name, self.url)
            return True
        except (Exception, BaseExceptionGroup) as exc:
            # anyio raises BaseExceptionGroup (not Exception) on transport
            # failures; boot must survive a down bridge. This is an *expected*
            # startup race (core boots before the bridge), so it is logged at
            # debug with the traceback and surfaced to the operator as one
            # concise warning — the full ExceptionGroup chain used to print four
            # times and bury real errors.
            log.debug("telegram MCP connect failed (%s)", self.url, exc_info=True)
            log.warning("telegram MCP unavailable (%s): %s", self.url, exc)
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
