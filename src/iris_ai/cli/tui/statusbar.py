"""One status line: name, model, session, and the last recall note."""

from __future__ import annotations


def render_status(
    *,
    name: str,
    provider: str,
    model: str,
    session: str,
    note: str = "",
) -> str:
    base = f"{name}   {provider}/{model}   session {session}"
    return f"{base}   {note}" if note else base
