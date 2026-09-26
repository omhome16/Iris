"""The action log: one record per attempt, and never what was typed."""

from __future__ import annotations

import json

from iris_ai.computer import Action, ActionKind, Observation
from iris_ai.computer.audit import ActionLog


def _entries(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_typed_secret_never_reaches_the_log(tmp_path):
    path = tmp_path / "config" / "actions.jsonl"
    secret = "hunter2-correct-horse-battery-staple"
    ActionLog(path).record(
        Action(ActionKind.TYPE, target="#password", window="Example", text=secret),
        Observation(kind=ActionKind.TYPE, ok=True, detail="typed 33 chars"),
        session="s",
        decision="allowed",
    )
    raw = path.read_text(encoding="utf-8")
    assert secret not in raw
    entry = _entries(path)[0]
    assert "text" not in entry
    assert entry["text_chars"] == len(secret)
    assert entry["text_hash"] and entry["text_hash"] != secret
    assert entry["credentials"] is True  # the field name says *why* it confirmed


def test_credentials_in_a_url_are_redacted(tmp_path):
    path = tmp_path / "config" / "actions.jsonl"
    ActionLog(path).record(
        Action(ActionKind.NAVIGATE, target="https://example.com/cb?token=abcdef0123456789abcdef"),
        Observation(kind=ActionKind.NAVIGATE, ok=True),
        session="s",
        decision="allowed",
    )
    raw = path.read_text(encoding="utf-8")
    assert "abcdef0123456789abcdef" not in raw


def test_each_record_is_one_line_so_the_file_is_append_only(tmp_path):
    path = tmp_path / "config" / "actions.jsonl"
    log = ActionLog(path)
    for _ in range(3):
        log.record(Action(ActionKind.SCREENSHOT), Observation(kind=ActionKind.SCREENSHOT, ok=True))
    assert len(_entries(path)) == 3


def test_an_unwritable_log_does_not_raise(tmp_path):
    """The action already happened; a failed record must not compound it."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    entry = ActionLog(blocker / "config" / "actions.jsonl").record(
        Action(ActionKind.SCREENSHOT), Observation(kind=ActionKind.SCREENSHOT, ok=True)
    )
    assert entry["action"] == "screenshot"  # returned even when the write failed
