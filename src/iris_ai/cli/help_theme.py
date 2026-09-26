"""Rich styling for the CLI. Plain output when piped or NO_COLOR is set."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "iris.ok": "bold green",
        "iris.warn": "bold yellow",
        "iris.fail": "bold red",
        "iris.title": "bold cyan",
    }
)


def console(stderr: bool = False) -> Console:
    return Console(theme=THEME, stderr=stderr)


LEVEL_STYLE = {"ok": "iris.ok", "warn": "iris.warn", "fail": "iris.fail"}
