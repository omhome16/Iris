"""One line for a tool call, collapsed by default."""

from __future__ import annotations


def render_tool(name: str, ok: bool | None, detail: str = "") -> str:
    if ok is None:
        mark = "..."
    elif ok:
        mark = "ok"
    else:
        mark = "failed"
    extra = f"  {detail}" if detail else ""
    return f"{name}  {mark}{extra}"
