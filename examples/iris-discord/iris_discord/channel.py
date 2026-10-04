"""Discord as a channel component.

`connect` logs the client in when a token is present. `receive` yields DMs and
mentions. Replies stream by edit, and the owner approves with buttons.
discord.py is imported only when no client was injected, so tests pass a fake.
"""

from __future__ import annotations

import asyncio

from iris_ai.channels.messages import InboundMessage, Sender


class DiscordChannel:
    name = "discord"
    capabilities = frozenset({"stream", "edit", "approve", "typing"})

    def __init__(self, token: str = "", *, client=None) -> None:
        self.token = token or _token()
        self._client = client
        self._queue: asyncio.Queue = asyncio.Queue()
        self._runner: asyncio.Task | None = None

    async def connect(self) -> None:
        if self._client is None:
            import discord

            self._client = _build_client(discord, self)
        start = getattr(self._client, "start", None)
        if self.token and start is not None:
            self._runner = asyncio.create_task(start(self.token))

    async def close(self) -> None:
        await self._queue.put(None)
        if self._runner is not None:
            self._runner.cancel()
        if self._client is not None and hasattr(self._client, "close"):
            await self._client.close()

    def inbound(self, message) -> InboundMessage | None:
        """DMs and mentions become turns. A thread is its own conversation."""
        return inbound_from(message, bot_id=_bot_id(self._client))

    async def receive(self):
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    def push(self, message) -> None:
        """Tests and the gateway handler both land here."""
        inbound = message if isinstance(message, InboundMessage) else self.inbound(message)
        if inbound is not None:
            self._queue.put_nowait(inbound)

    async def send(self, message) -> str:
        text = getattr(message, "text", str(message))
        conversation = getattr(message, "conversation", "")
        channel = self._channel(conversation)
        sent = await channel.send(text)
        return str(getattr(sent, "id", "") or "")

    async def edit(self, message_id: str, text: str) -> str:
        editor = getattr(self._client, "edit_message", None)
        if editor is not None:
            await editor(message_id, text)
        return message_id

    async def ask_approval(self, prompt: str) -> str:
        channel = self._channel("owner")
        view = {"components": [{"custom_id": "iris-approve"}, {"custom_id": "iris-deny"}]}
        await channel.send(prompt, view=view)
        return "pending"

    async def typing(self, conversation: str) -> None:
        channel = self._channel(conversation)
        async with channel.typing():
            return None

    def _channel(self, conversation: str):
        getter = getattr(self._client, "get_channel", None)
        if getter is None:
            raise RuntimeError("discord is not connected")
        found = getter(conversation)
        if found is None:
            raise RuntimeError(f"no discord conversation {conversation}")
        return found


def _token() -> str:
    try:
        from iris_ai.secrets import lookup

        value, _where = lookup("DISCORD_BOT_TOKEN")
    except Exception:  # noqa: BLE001 - a missing store means no token yet
        return ""
    return value or ""


def _build_client(discord, channel: DiscordChannel):
    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_message(message) -> None:
        channel.push(message)

    return client


def _bot_id(client) -> str:
    user = getattr(client, "user", None)
    return str(getattr(user, "id", "") or "")


def inbound_from(message, *, bot_id: str = "") -> InboundMessage | None:
    """A DM, or a mention. Other guild traffic is ignored."""
    author = getattr(message, "author", None)
    author_id = str(getattr(author, "id", "") or "")
    if not author_id or (bot_id and author_id == bot_id):
        return None
    content = str(getattr(message, "content", "") or "")
    guild = getattr(message, "guild", None)
    mentions = list(getattr(message, "mentions", []) or [])
    mentioned = any(str(getattr(item, "id", "")) == bot_id for item in mentions) if bot_id else False
    if guild is not None and not mentioned:
        return None
    channel = getattr(message, "channel", None)
    channel_id = str(getattr(channel, "id", "") or "dm")
    thread = getattr(message, "thread", None)
    if thread is not None:
        conversation = f"thread:{getattr(thread, 'id', channel_id)}"
    elif guild is None:
        conversation = f"dm:{channel_id}"
    else:
        conversation = channel_id
    display = str(getattr(author, "display_name", "") or author_id)
    return InboundMessage(
        channel="discord",
        conversation=conversation,
        sender=Sender(id=author_id, display=display),
        text=content,
        message_id=str(getattr(message, "id", "") or ""),
    )


def decision_from_interaction(custom_id: str) -> str:
    """Button ids the approval view posts."""
    if custom_id == "iris-approve":
        return "approved"
    if custom_id == "iris-deny":
        return "denied"
    return ""


async def stream_edit(edit, message_id: str, chunks: list[str], *, allow) -> str:
    """Edit the same message as chunks arrive. `allow(now)` is the throttle."""
    text = ""
    now = 0.0
    for chunk in chunks:
        text += chunk
        now += 0.4
        if allow(now):
            await edit(message_id, text)
    await edit(message_id, text)
    return text
