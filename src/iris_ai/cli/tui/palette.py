"""Slash commands the chat screen understands."""

from __future__ import annotations

COMMANDS: tuple[tuple[str, str], ...] = (
    ("/help", "list these commands"),
    ("/new", "start a fresh session"),
    ("/sessions", "list saved sessions"),
    ("/switch", "continue another session: /switch <name>"),
    ("/model", "show the model in use"),
    ("/provider", "show the provider in use"),
    ("/persona", "show the persona file"),
    ("/config", "open setup again"),
    ("/memory", "where memory lives"),
    ("/search", "search memory: /search <query>"),
    ("/dream", "run consolidation now"),
    ("/forget", "how to forget a memory"),
    ("/tools", "list the tools the assistant can call"),
    ("/skills", "list installed skills"),
    ("/costs", "what this process has spent"),
    ("/trace", "where the trace log is"),
    ("/clear", "clear the transcript on screen"),
    ("/exit", "leave the chat"),
)


def matching(prefix: str) -> list[tuple[str, str]]:
    text = prefix.strip().lower()
    if not text.startswith("/"):
        return []
    return [item for item in COMMANDS if item[0].startswith(text)]
