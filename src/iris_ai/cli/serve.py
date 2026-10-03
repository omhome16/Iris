"""Start a face: the terminal, Telegram, or the HTTP API."""

from __future__ import annotations

import os

from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.security import host_is_loopback


def run(channel: str = "terminal", *, host: str = "127.0.0.1", port: int = 8000, insecure: bool = False) -> int:
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
        public = not host_is_loopback(host)
        if public and not settings.iris_api_token and not insecure and os.environ.get("IRIS_HTTP_INSECURE", "").strip() != "1":
            ui.failed(
                out,
                "refusing to listen",
                f"{host} has no IRIS_API_TOKEN. Set the token, or pass --insecure.",
            )
            return 1
        if insecure:
            os.environ["IRIS_HTTP_INSECURE"] = "1"
        ui.note(out, f"serving the HTTP API on http://{host}:{port}")
        uvicorn.run("iris_ai.api:app", host=host, port=port)
        return 0
    if name == "telegram":
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or settings.telegram_bot_token.strip()
        if not token:
            ui.failed(out, "TELEGRAM_BOT_TOKEN is unset", "set it in .env, then start the bridge")
            return 1
        ui.note(out, "Telegram uses the bridge. The token is set; start the bridge process.")
        return 0
    ui.failed(out, "unknown channel", f"{channel!r}. known: terminal, telegram, http")
    return 2
