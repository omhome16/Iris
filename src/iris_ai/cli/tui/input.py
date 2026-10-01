"""The bottom prompt. A thin wrapper so the screen can swap it later."""

from __future__ import annotations


def prompt_for(assistant: str) -> str:
    return f"{assistant} > "
