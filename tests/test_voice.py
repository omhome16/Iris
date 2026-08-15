"""Voice tests — transcription wrapper (no live API calls).

The happy path needs a real Groq key, so we test the contract instead:
not-configured error, size cap, and that a successful transcription is
passed through (stubbed litellm).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris.config import settings
from iris.voice import transcribe


async def test_transcribe_without_groq_key_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "groq_api_key", "")
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio bytes")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        await transcribe(audio)


async def test_transcribe_rejects_huge_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "groq_api_key", "sk-test")
    audio = tmp_path / "huge.ogg"
    audio.write_bytes(b"x" * (26 * 1024 * 1024))
    with pytest.raises(RuntimeError, match="too large"):
        await transcribe(audio)


async def test_transcribe_passes_through_model_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "groq_api_key", "sk-test")

    class FakeResp:
        text = "the owner said hello in a voice note"

    async def fake_transcription(**kwargs):
        assert kwargs["model"] == "groq/whisper-large-v3-turbo"
        assert kwargs["api_key"] == "sk-test"
        return FakeResp()

    monkeypatch.setattr("iris.voice.litellm.atranscription", fake_transcription)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio bytes")

    assert await transcribe(audio) == "the owner said hello in a voice note"


async def test_transcribe_empty_transcript_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "groq_api_key", "sk-test")

    class FakeResp:
        text = "   "

    async def fake_transcription(**kwargs):
        return FakeResp()

    monkeypatch.setattr("iris.voice.litellm.atranscription", fake_transcription)
    audio = tmp_path / "voice.ogg"
    audio.write_bytes(b"fake audio bytes")
    with pytest.raises(RuntimeError, match="empty"):
        await transcribe(audio)