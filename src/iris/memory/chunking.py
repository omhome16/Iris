"""Token-aware chunking for the memory index.

Pattern from the research: recursive-ish splitting with overlap (~400 tokens,
80 overlap — the OpenClaw/parent-child family). Files are the source of truth;
chunks are a derived view stored in Postgres.

Contextual retrieval (Anthropic-style): a cheap-model pass writes a short
context header (30–60 tokens) per chunk, so the embedded vector carries
document-level context instead of a bare paragraph. Headers are cached per
file by content hash in `.dreams/contexts/`; plain chunks are the fallback
whenever the model is unavailable or the answer does not parse.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from iris.config import settings
from iris.memory.llm import LLMClient

_TOKEN_RE = re.compile(r"\S+")

_CONTEXT_SYSTEM = """You write context headers for memory chunks. Given a
document split into numbered chunks, write ONE short context header per chunk
(30-60 words): a self-contained sentence that says what the chunk is about
*in the context of the whole document* — the document topic, who/what/when,
and how the chunk fits in. No numbering, no preamble, no markdown.

Respond ONLY with JSON: {"contexts": [{"chunk": 0, "context": "..."}]}"""


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


def _truncate_tokens(text: str, max_tokens: int) -> str:
    tokens = _TOKEN_RE.findall(text)
    return " ".join(tokens[:max_tokens])


def _file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_path(cache_dir: Path, text: str) -> Path:
    return cache_dir / f"{_file_hash(text)[:16]}.json"


def load_context_cache(cache_dir: Path, text: str) -> dict[int, str] | None:
    """Headers for this exact file content, or None on any mismatch/miss."""
    path = _cache_path(cache_dir, text)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("hash") != _file_hash(text):
        return None
    out: dict[int, str] = {}
    for item in data.get("contexts", []):
        try:
            out[int(item["chunk"])] = str(item["context"])
        except (KeyError, TypeError, ValueError):
            continue
    return out or None


def store_context_cache(cache_dir: Path, text: str, contexts: dict[int, str]) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, text)
    try:
        path.write_text(
            json.dumps(
                {
                    "hash": _file_hash(text),
                    "contexts": [{"chunk": i, "context": ctx} for i, ctx in sorted(contexts.items())],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


async def _ask_for_contexts(
    llm: LLMClient, chunks: list[str], *, header_tokens: int, batch_size: int = 20
) -> dict[int, str]:
    """Cheap-model pass over chunk batches. Returns {chunk_index: context}."""
    contexts: dict[int, str] = {}
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        payload = await llm.complete(
            [
                {"role": "system", "content": _CONTEXT_SYSTEM},
                {
                    "role": "user",
                    "content": "\n\n".join(
                        f"[chunk {start + i}] {_truncate_tokens(c, 200)}" for i, c in enumerate(batch)
                    ),
                },
            ],
            tier="cheap",
            json_mode=True,
            max_tokens=1500,
        )
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for item in data.get("contexts", []):
            try:
                i = int(item["chunk"])
            except (KeyError, TypeError, ValueError):
                continue
            if not 0 <= i < len(chunks):
                continue
            ctx = _truncate_tokens(str(item.get("context", "")).strip(), header_tokens)
            if ctx:
                contexts[i] = ctx
    return contexts


async def contextualize_chunks(
    chunks: list[str],
    *,
    llm: LLMClient | None,
    cache_dir: Path,
    text: str,
    enabled: bool | None = None,
) -> list[str]:
    """Merge cheap-model context headers into chunks (plain fallback).

    Cache-first: if headers exist for this exact file content, reuse them and
    never call the model. Any failure (disabled, no llm, bad JSON, empty
    result) degrades to the plain chunks — never raises.
    """
    enabled = settings.contextual_chunking_enabled if enabled is None else enabled
    if not enabled or not llm or not chunks:
        return chunks
    cached = load_context_cache(cache_dir, text)
    if cached is not None:
        contexts = cached
    else:
        header_tokens = settings.context_header_tokens
        contexts = await _ask_for_contexts(llm, chunks, header_tokens=header_tokens)
        if not contexts:
            return chunks
        store_context_cache(cache_dir, text, contexts)
    return [
        f"{contexts.get(i, '')}\n\n{chunk}".strip() if i in contexts else chunk
        for i, chunk in enumerate(chunks)
    ]
