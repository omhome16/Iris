"""One line for a tool call. It starts open and collapses when the tool finishes."""

from __future__ import annotations

from textual.widgets import Static


def render_tool(name: str, ok: bool | None, detail: str = "") -> str:
    if ok is None:
        mark = "..."
    elif ok:
        mark = "ok"
    else:
        mark = "failed"
    extra = f"  {detail}" if detail else ""
    return f"{name}  {mark}{extra}"


class ToolCard(Static):
    """A tool line that updates in place when the result arrives."""

    def __init__(self, name: str) -> None:
        self.tool_name = name
        self.plain = render_tool(name, None)
        super().__init__(self.plain, classes="tool-card")

    def finish(self, *, ok: bool, summary: str, duration_ms: float) -> None:
        detail = f"{duration_ms:.0f}ms  {summary}".strip() if duration_ms else summary
        self.plain = render_tool(self.tool_name, ok, detail)
        self.update(self.plain)
        self.set_class(ok, "tool-ok")
        self.set_class(not ok, "tool-fail")
