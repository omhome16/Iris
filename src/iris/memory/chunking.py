"""Token-aware chunking for the memory index.

Pattern from the research: recursive-ish splitting with overlap (~400 tokens,
80 overlap — the OpenClaw/parent-child family). Files are the source of truth;
chunks are a derived view stored in Postgres.
"""

from __future__ import annotations

import re

from iris.config import settings

_TOKEN_RE = re.compile(r"\S+")


def estimate_tokens(text: str) -> int:
    return len(_TOKEN_RE.findall(text))


def chunk_text(text: str, *, chunk_tokens: int | None = None, overlap_tokens: int | None = None) -> list[str]:
    chunk_tokens = chunk_tokens or settings.chunk_tokens
    overlap_tokens = overlap_tokens if overlap_tokens is not None else settings.chunk_overlap_tokens
    tokens = _TOKEN_RE.findall(text)
    if not tokens:
        return []
    chunks: list[str] = []
    step = max(1, chunk_tokens - overlap_tokens)
    for start in range(0, len(tokens), step):
        piece = tokens[start : start + chunk_tokens]
        chunks.append(" ".join(piece))
        if start + chunk_tokens >= len(tokens):
            break
    return chunks