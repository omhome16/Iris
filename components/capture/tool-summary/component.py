"""Capture only what a tool did.

A decision in the user's words is ignored. A tool call on the request, or a
`tool <name>: <result>` line in the reply, becomes one summary candidate.
The decision-signal suite has no tool calls, so this stage stores nothing
there. That is the mechanism, not a missed decision.
"""

from __future__ import annotations

import re

_LINE = re.compile(r"^tool\s+([A-Za-z0-9_]+)\s*:\s*(.+)$", re.IGNORECASE)


def _calls(request) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    raw = getattr(request, "tool_calls", None) or []
    for call in raw:
        if isinstance(call, dict):
            name = str(call.get("name") or "")
            result = str(call.get("result") or call.get("args") or "")
        else:
            name = str(getattr(call, "name", "") or "")
            result = str(getattr(call, "result", "") or "")
        if name:
            found.append((name, result))
    reply = str(getattr(request, "reply", "") or "")
    user = str(getattr(request, "user_message", "") or "")
    for line in f"{user}\n{reply}".splitlines():
        match = _LINE.match(line.strip())
        if match:
            found.append((match.group(1), match.group(2).strip()))
    return found


class ToolSummary:
    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options

    async def extract(self, request):
        from iris_ai.sdk.types import MemoryCandidate

        calls = _calls(request)
        if not calls:
            return []
        lines = [f"{name} -> {result}".strip() for name, result in calls]
        summary = "; ".join(line for line in lines if line and not line.endswith("->"))
        if not summary:
            summary = "; ".join(name for name, _result in calls)
        return [MemoryCandidate(content=summary, kind="tool", importance=4, reason="tool call")]
