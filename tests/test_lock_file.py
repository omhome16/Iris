"""The components lock is not stolen from a live writer."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from iris_ai.components.lock import pin, read_lock


def _use(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\n", encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(harness))
    monkeypatch.setattr("iris_ai.components.lock._LOCK_TIMEOUT", 0.05)
    return harness.parent / "components.lock"


def test_a_live_sidecar_is_not_stolen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock = _use(tmp_path, monkeypatch)
    flag = lock.with_name(lock.name + ".lock")
    flag.write_text(str(os.getpid()), encoding="utf-8")
    with pytest.raises(TimeoutError):
        pin("persona", "voice", source="local", digest="abc", path=lock)
    assert "kinds" not in read_lock(lock) or read_lock(lock)["kinds"] == {}
    assert flag.read_text(encoding="utf-8").strip() == str(os.getpid())


def test_a_dead_sidecar_can_be_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    lock = _use(tmp_path, monkeypatch)
    flag = lock.with_name(lock.name + ".lock")
    flag.write_text("999999999", encoding="utf-8")
    pin("persona", "voice", source="local", digest="abc", path=lock)
    assert read_lock(lock)["kinds"]["persona"]["active"] == "voice"
    assert not flag.exists()
