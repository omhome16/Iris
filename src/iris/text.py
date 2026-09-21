"""Shared text helpers.

`text_of` was duplicated in `agent/chat.py`, `api.py` and `onboarding.py` with
subtly different behaviour for image content blocks. One copy, one behaviour.
"""

from __future__ import annotations


def text_of(content: object) -> str:
    """Plain text from a message content that may be a string or a list of
    content blocks (OpenAI image/text format)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", ""))
            for part in content
            if isinstance(part, dict) and part.get("text")
        )
    return ""
