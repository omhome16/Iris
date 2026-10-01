"""Append-only transcript lines for the chat screen."""

from __future__ import annotations

from iris_ai.cli.tui.tool_card import render_tool


def user_line(text: str) -> str:
    return f"you  {text}"


def assistant_line(name: str, text: str) -> str:
    return f"{name}  {text}"


def tool_line(name: str, ok: bool | None, detail: str = "") -> str:
    return render_tool(name, ok, detail)


def note_line(text: str) -> str:
    return text
