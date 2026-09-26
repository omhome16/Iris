"""Telegram update handling: normalization + idempotent delivery.

The bridge kept its poll offset **in memory only**, so a restart replayed
unconfirmed updates — and every replay ran a brand-new turn through the graph.
These tests pin the fix.
"""

from __future__ import annotations

import json

from iris.channels.updates import InboundUpdate, UpdateLedger, normalize_update


def _update(update_id: int, *, text: str | None = None, **message_extra) -> dict:
    message: dict = {"chat": {"id": 42}}
    if text is not None:
        message["text"] = text
    message.update(message_extra)
    return {"update_id": update_id, "message": message}


def test_normalize_update_classifies_a_plain_message():
    update = normalize_update(_update(1, text="hello there"))
    assert isinstance(update, InboundUpdate)
    assert (update.update_id, update.chat_id, update.kind, update.text) == (1, 42, "text", "hello there")


def test_normalize_update_classifies_a_command():
    update = normalize_update(_update(2, text="/mind"))
    assert update is not None
    assert update.kind == "command"


def test_normalize_update_classifies_photo_with_caption():
    update = normalize_update(_update(3, photo=[{"file_id": "f"}], caption="what is this?"))
    assert update is not None
    assert update.kind == "photo"
    assert update.caption == "what is this?"
    assert update.text == ""


def test_normalize_update_classifies_voice():
    update = normalize_update(_update(4, voice={"file_id": "v"}))
    assert update is not None
    assert update.kind == "voice"


def test_normalize_update_accepts_edited_message():
    update = normalize_update({"update_id": 5, "edited_message": {"chat": {"id": 7}, "text": "fixed"}})
    assert update is not None
    assert update.chat_id == 7


def test_normalize_update_ignores_what_it_cannot_handle():
    for payload in (
        {"update_id": 6},  # no message at all
        {},  # nothing
        {"update_id": 7, "message": {"chat": {}}},  # no chat id
        {"update_id": "not-an-int", "message": {"chat": {"id": 1}, "text": "x"}},
    ):
        assert normalize_update(payload) is None


def test_normalize_update_marks_unanswerable_messages_other():
    """A sticker (or any message with nothing to read) is still an update the
    bridge must acknowledge — it becomes `other`, which the caller replies to
    with "I can only read text, voice, or photos for now."""
    for payload in (
        {"update_id": 8, "message": {"chat": {"id": 1}}},  # no text/photo/voice
        {"update_id": 9, "message": {"chat": {"id": 1}, "sticker": {"file_id": "s"}}},
    ):
        update = normalize_update(payload)
        assert update is not None
        assert update.kind == "other"
        assert update.chat_id == 1


def test_ledger_ignores_a_replayed_update(tmp_path):
    """The bug this exists for: a redelivered update must not run a second turn."""
    ledger = UpdateLedger(tmp_path / "updates.json")
    ledger.load()
    assert ledger.next_offset() == 0
    assert ledger.is_duplicate(10) is False
    ledger.mark_processed(10)
    ledger.save()
    assert ledger.is_duplicate(10) is True
    assert ledger.next_offset() == 11


def test_ledger_survives_a_restart(tmp_path):
    path = tmp_path / "updates.json"
    first = UpdateLedger(path)
    first.load()
    for update_id in (10, 11, 12):
        first.mark_processed(update_id)
    first.save()

    restarted = UpdateLedger(path)
    restarted.load()
    assert restarted.next_offset() == 13
    assert all(restarted.is_duplicate(i) for i in (10, 11, 12))
    assert restarted.is_duplicate(13) is False


def test_ledger_is_monotonic_and_keeps_the_highest(tmp_path):
    ledger = UpdateLedger(tmp_path / "updates.json")
    ledger.load()
    ledger.mark_processed(20)
    ledger.mark_processed(12)  # an older id must not rewind the offset
    assert ledger.next_offset() == 21


def test_ledger_tolerates_missing_and_corrupt_files(tmp_path):
    missing = tmp_path / "nope.json"
    ledger = UpdateLedger(missing)
    ledger.load()  # must not raise
    assert ledger.next_offset() == 0

    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not json", encoding="utf-8")
    ledger = UpdateLedger(corrupt)
    ledger.load()  # must not raise
    assert ledger.next_offset() == 0


def test_ledger_writes_atomically(tmp_path):
    path = tmp_path / "data" / "updates.json"
    ledger = UpdateLedger(path)
    ledger.load()
    ledger.mark_processed(3)
    ledger.save()
    assert json.loads(path.read_text(encoding="utf-8"))["last_processed"] == 3
    assert list(path.parent.glob("*.tmp")) == []  # no half-written leftovers
