"""Slash commands the chat screen understands."""

from __future__ import annotations

from iris_ai.commands import COMMANDS as _ALL

# The full-screen list is every command except ones only the plain REPL handles.
COMMANDS: tuple[tuple[str, str], ...] = tuple(
    (name, blurb) for name, blurb in _ALL if name not in {"/conflicts", "/quit"}
)


def matching(prefix: str) -> list[tuple[str, str]]:
    text = prefix.strip().lower()
    if not text.startswith("/"):
        return []
    return [item for item in COMMANDS if item[0].startswith(text)]
