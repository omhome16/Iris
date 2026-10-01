"""One status line: name, model, session, and the last recall note."""

from __future__ import annotations


def render_status(
    *,
    name: str,
    provider: str,
    model: str,
    session: str,
    note: str = "",
    tokens: int = 0,
    cost: float = 0.0,
) -> str:
    base = f"{name}   {provider}/{model}   session {session}"
    if tokens or cost:
        base = f"{base}   {tokens} tok   ${cost:.4f}"
    return f"{base}   {note}" if note else base
