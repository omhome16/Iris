"""Declarative commands: `components/command/<name>/prompt.md`.

The REPL looks here before it sends a slash line to the model. A name that
the built-in command list already owns is left alone.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.commands import REPL, names
from iris_ai.plug import components_root


def expand(text: str, *, root: Path | None = None) -> str | None:
    """The prompt body, or None when this line is not a declarative command."""
    stripped = text.strip()
    if not stripped.startswith("/") or stripped.startswith("//"):
        return None
    head, _, rest = stripped[1:].partition(" ")
    if not head or "/" in head or "\\" in head or head in {".", ".."}:
        return None
    token = f"/{head}"
    if token in names() or token in REPL:
        return None
    prompt = components_root(root) / "command" / head / "prompt.md"
    if not prompt.is_file():
        return None
    return prompt.read_text(encoding="utf-8").replace("$args", rest.strip())
