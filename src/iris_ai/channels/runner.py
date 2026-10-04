"""One runner for every channel.

Identity, the session id, dedupe, and the rate limit live here. A channel
only turns a platform event into an `InboundMessage` and delivers the reply.
"""

from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from iris_ai.channels.messages import InboundMessage


class ChannelRunner:
    def __init__(
        self,
        *,
        channel: str,
        owners: tuple[str, ...] = (),
        others: str = "untrusted",
        rate_per_minute: int = 20,
        ledger: Path | None = None,
    ) -> None:
        if others not in {"untrusted", "ignore"}:
            raise ValueError("others must be untrusted or ignore")
        self.channel = channel
        self.owners = set(owners)
        self.others = others
        self.rate_per_minute = rate_per_minute
        self.ledger = Path(ledger) if ledger is not None else None
        self.seen: set[str] = set()
        self._hits: dict[str, deque[float]] = {}
        self._busy: set[str] = set()
        self._last_edit = -1.0
        if self.ledger is not None and self.ledger.is_file():
            self.seen = {line.strip() for line in self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()}

    def origin_for(self, sender_id: str) -> str | None:
        """`None` means drop the message. Otherwise the turn origin."""
        if sender_id in self.owners:
            return "owner"
        if self.others == "ignore":
            return None
        return "untrusted"

    def session_for(self, message: InboundMessage) -> str:
        return f"{message.channel}:{message.conversation}"

    def accept(self, message: InboundMessage, *, now: float | None = None) -> str | None:
        """Return the origin, or None when the message is a duplicate or over the limit."""
        if message.message_id and message.message_id in self.seen:
            return None
        origin = self.origin_for(message.sender.id)
        if origin is None:
            return None
        if not self._allow(message.sender.id, now=now if now is not None else time.monotonic()):
            return None
        if message.message_id:
            self.seen.add(message.message_id)
            self._save()
        return origin

    def begin(self, session: str) -> bool:
        """One turn at a time per session."""
        if session in self._busy:
            return False
        self._busy.add(session)
        return True

    def end(self, session: str) -> None:
        self._busy.discard(session)

    def allow_edit(self, now: float, *, interval: float = 1.0) -> bool:
        """Throttle streamed edits so a channel is not rewritten every token."""
        if now - self._last_edit < interval:
            return False
        self._last_edit = now
        return True

    def backoff(self, attempt: int) -> float:
        """Reconnect delay in seconds, capped at 30."""
        return min(30.0, 0.5 * (2 ** max(0, attempt)))

    async def approval(self, channel, sender_id: str, prompt: str) -> str:
        """Owner approvals use the channel. Everyone else is refused."""
        if sender_id not in self.owners:
            return "denied"
        if "approve" not in getattr(channel, "capabilities", frozenset()):
            return "denied: use iris chat"
        ask = getattr(channel, "ask_approval", None)
        if ask is None:
            return "denied: use iris chat"
        return await ask(prompt)

    def _save(self) -> None:
        if self.ledger is None:
            return
        self.ledger.parent.mkdir(parents=True, exist_ok=True)
        self.ledger.write_text("\n".join(sorted(self.seen)) + "\n", encoding="utf-8")

    def _allow(self, sender_id: str, *, now: float) -> bool:
        window = self._hits.setdefault(sender_id, deque())
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.rate_per_minute:
            return False
        window.append(now)
        return True


def notify(channel: str, conversation: str, text: str) -> dict[str, str]:
    """One delivery shape for every channel. The v0 adapter still sends Telegram."""
    return {"channel": channel, "conversation": str(conversation), "text": text}
