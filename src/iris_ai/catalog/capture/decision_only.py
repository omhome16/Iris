"""Keep decisions. Drop questions, hypotheticals, and small talk."""

from __future__ import annotations

import re

_KEEP = re.compile(
    r"\b(from now on|we decided|instead of|always use|never use|i prefer|i have moved)\b",
    re.IGNORECASE,
)
_DROP = re.compile(r"\b(thinking about|someday|\?)\b", re.IGNORECASE)


def keep(text: str) -> bool:
    if _DROP.search(text):
        return False
    return _KEEP.search(text) is not None


class DecisionOnly:
    api_version = "iris/v1"
    permissions = ()

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx

    async def extract(self, request):
        from iris_ai.sdk.types import MemoryCandidate

        text = f"{getattr(request, 'user_message', '')} {getattr(request, 'reply', '')}"
        if not keep(text):
            return []
        kind = "preference" if re.search(r"prefer|moved", text, re.I) else "decision"
        return [MemoryCandidate(content=getattr(request, "user_message", text).strip(), kind=kind, importance=7)]

    async def maybe_capture(self, *, user_message: str, reply: str, known_context: str = "") -> str | None:
        if keep(user_message):
            return user_message.strip()
        return None
