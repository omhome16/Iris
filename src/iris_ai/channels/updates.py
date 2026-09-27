"""Telegram updates: normalization, and delivery that cannot double-fire.

Two jobs, both of which used to be inline in the bridge's poll loop:

- `normalize_update` turns a raw Telegram update dict into an `InboundUpdate`
  (or `None` for updates this bridge does not handle), so the loop stops
  branching on dict shapes.
- `UpdateLedger` remembers what was already handled, **on disk**. The bridge
  kept its offset in memory (`offset = 0` every boot), so a restart replayed
  every unconfirmed update — and each replay ran a new turn through the graph.

Why a single monotonic watermark is enough: the poll loop is sequential and
awaits each update before marking it, and the offset passed back to Telegram is
`last_processed + 1`. So an update is a duplicate exactly when its id is at or
below the watermark, and a failed update simply never advances it — it gets
redelivered rather than skipped. (A bounded "recent ids" set would be redundant
with a sequential loop; it is deliberately not here.)

Dependency rule: stdlib only — see `iris/channels/brain.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

UpdateKind = Literal["text", "command", "photo", "voice", "other"]


@dataclass(slots=True)
class InboundUpdate:
    """One Telegram update, normalized."""

    update_id: int
    chat_id: int
    kind: UpdateKind
    text: str = ""
    caption: str = ""
    photo: list | None = None
    voice: dict | None = None
    raw: dict = field(default_factory=dict)


def normalize_update(update: object) -> InboundUpdate | None:
    """Raw Telegram update → `InboundUpdate`, or `None` if it is not ours.

    Commands are recognized *before* text because they are handled by a
    different code path (the command dispatcher, not the graph).
    """
    if not isinstance(update, dict):
        return None
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return None

    message = update.get("message") or update.get("edited_message")
    if not isinstance(message, dict):
        return None
    chat_id = (message.get("chat") or {}).get("id")
    if not isinstance(chat_id, int):
        return None

    text = str(message.get("text") or "").strip()
    caption = str(message.get("caption") or "").strip()
    photo = message.get("photo") if isinstance(message.get("photo"), list) else None
    voice = message.get("voice") if isinstance(message.get("voice"), dict) else None
    if text.startswith("/"):
        kind: UpdateKind = "command"
    elif text:
        kind = "text"
    elif photo:
        kind = "photo"
    elif voice:
        kind = "voice"
    else:
        # A message we cannot answer (sticker, document…). Still an update the
        # bridge must acknowledge, and the caller replies to say so.
        kind = "other"

    return InboundUpdate(
        update_id=update_id,
        chat_id=chat_id,
        kind=kind,
        text=text,
        caption=caption,
        photo=photo,
        voice=voice,
        raw=update,
    )


class UpdateLedger:
    """Highest handled update id + "already handled" test, persisted to JSON."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.last_processed = 0

    def load(self) -> None:
        """Read the ledger; a missing or corrupt file starts a fresh one.

        Failing closed here would mean never starting the bridge because of a
        truncated file — the cost of a lost ledger is one redelivered batch.
        """
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        value = data.get("last_processed") if isinstance(data, dict) else None
        if isinstance(value, int) and value >= 0:
            self.last_processed = value

    def save(self) -> None:
        """Atomic write: a crash can lose the ledger, never half-write it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps({"last_processed": self.last_processed}), encoding="utf-8")
        os.replace(tmp, self.path)

    def is_duplicate(self, update_id: int) -> bool:
        return update_id <= self.last_processed

    def mark_processed(self, update_id: int) -> None:
        """Record a *completed* update. Never rewinds (Telegram can deliver out of order)."""
        self.last_processed = max(self.last_processed, update_id)

    def next_offset(self) -> int:
        """The offset to hand Telegram: everything up to here is acknowledged.

        A fresh ledger returns 0 — nothing has been handled, so nothing may be
        acknowledged away.
        """
        if self.last_processed == 0:
            return 0
        return self.last_processed + 1
