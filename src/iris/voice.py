"""Voice — voice-note transcription via Groq Whisper (free tier).

The bridge downloads the Telegram voice message and posts the audio file to
`POST /voice`; this module transcribes it with Groq's whisper-large-v3-turbo
and the graph runs on the transcript. Needs GROQ_API_KEY — without it the
endpoint answers with a clear "not configured" instead of failing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import litellm

from iris.config import settings

log = logging.getLogger("iris.voice")

VOICE_MODEL = "groq/whisper-large-v3-turbo"
MAX_AUDIO_MB = 20  # Telegram bot API hard limit (was 25 — oversize uploads were rejected server-side)


async def transcribe(audio_path: Path) -> str:
    """Transcribe an audio file to text. Raises RuntimeError when Groq is
    not configured or the model rejects the file."""
    if not settings.groq_api_key:
        raise RuntimeError(
            "voice notes need GROQ_API_KEY — set it in .env (free tier at console.groq.com)"
        )
    if audio_path.stat().st_size > MAX_AUDIO_MB * 1024 * 1024:
        raise RuntimeError(f"audio too large (max {MAX_AUDIO_MB} MB)")
    with audio_path.open("rb") as f:
        try:
            resp = await litellm.atranscription(
                model=settings.voice_model,
                file=f,
                api_key=settings.groq_api_key,
            )
        except Exception as exc:
            log.warning("transcription failed: %s", exc)
            raise RuntimeError(f"transcription failed: {type(exc).__name__}") from exc
    text = (resp.text or "").strip()
    if not text:
        raise RuntimeError("transcription came back empty — try again")
    return text
