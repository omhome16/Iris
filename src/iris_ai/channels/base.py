"""Channel protocol — what every outbound channel must satisfy.

A *channel* is how Iris reaches the owner: Telegram over MCP today, Discord,
Slack, a webhook or a bespoke transport later. The contract is deliberately
small and transport-agnostic, because it is the *floor*, not the ceiling:

- send text, send a photo, read back a transcript;
- report whether it is connected, connect once, close cleanly.

Anything richer (reactions, threads, attachments) is a tool a plugin registers
against the same runtime — not something every channel is forced to fake. A
transport that only implements the floor is still a first-class channel.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Channel(Protocol):
    """The minimum a transport must provide to be an Iris channel."""

    name: str

    @property
    def connected(self) -> bool:
        """True once `connect()` has succeeded and the session is still open."""
        ...

    async def connect(self) -> bool:
        """Open the session. Idempotent; returns False rather than raising when
        the transport is down (boot must survive a channel that is not up yet)."""
        ...

    async def close(self) -> None:
        """Release the session. Never raises during shutdown."""
        ...

    async def send_message(self, chat_id: int, text: str) -> str:
        """Deliver a text message. Returns the transport's own receipt/reason."""
        ...

    async def send_photo(self, chat_id: int, photo_url: str, caption: str = "") -> str:
        """Deliver an image by URL. Returns the transport's own receipt/reason."""
        ...

    async def get_chat_history(self, chat_id: int, limit: int = 10) -> str:
        """Read back recent messages, for context the memory files do not hold."""
        ...
