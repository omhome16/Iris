"""Start a face: the terminal, Telegram, or the HTTP API."""

from __future__ import annotations

from iris_ai.cli import ui
from iris_ai.cli.help_theme import console


def run(channel: str = "terminal") -> int:
    name = (channel or "terminal").strip().lower()
    out = console()
    if name in {"terminal", "tui", "chat"}:
        from iris_ai.cli.chat import run_chat

        return run_chat()
    if name in {"http", "api", "webhook"}:
        try:
            import uvicorn
        except ImportError:
            ui.failed(out, "the HTTP API needs the api extra", "uv sync --extra api")
            return 1
        ui.note(out, "serving the HTTP API on http://127.0.0.1:8000")
        uvicorn.run("iris_ai.api:app", host="127.0.0.1", port=8000)
        return 0
    if name == "telegram":
        ui.note(out, "Telegram uses the bridge. Set the bot token, then run the bridge process.")
        ui.note(out, "fix: uv sync --extra mcp  and set TELEGRAM_BOT_TOKEN in .env")
        return 0
    ui.failed(out, "unknown channel", f"{channel!r}. known: terminal, telegram, http")
    return 2
