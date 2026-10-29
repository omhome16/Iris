"""Recency-only memory. The deliberate floor under the fused scorer.

A hit has to contain the query. Among those hits the newest write wins.
There is no vector term, no importance, and no decay curve. `nearest` still
answers cosine, because dreaming's dedupe is not a ranking policy.
"""

from __future__ import annotations

import hashlib
import math


def _vector(text: str) -> list[float]:
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [byte / 255 for byte in digest[:32]]


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    left_norm = math.sqrt(sum(item * item for item in left))
    right_norm = math.sqrt(sum(item * item for item in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def _row(record: object, seq: int) -> dict:
    if isinstance(record, dict):
        content = str(record.get("content") or "")
        path = str(record.get("path") or "")
        index = int(record.get("chunk_index") or 0)
        observed = str(record.get("observed_at") or "")
        stale = bool(record.get("stale"))
    else:
        content = str(getattr(record, "content", "") or "")
        path = str(getattr(record, "path", "") or "")
        index = int(getattr(record, "chunk_index", 0) or 0)
        provenance = getattr(record, "provenance", None)
        observed = str(getattr(provenance, "observed_at", "") or "") if provenance is not None else ""
        stale = False
    return {
        "content": content,
        "path": path,
        "chunk_index": index,
        "observed_at": observed,
        "stale": stale,
        "seq": seq,
        "vector": _vector(content),
    }


def _matches(query: str, content: str) -> bool:
    needle = (query or "").casefold().strip()
    if not needle:
        return False
    haystack = content.casefold()
    if needle in haystack:
        return True
    tokens = [token for token in needle.split() if len(token) >= 3]
    return bool(tokens) and all(token in haystack for token in tokens)


class Recency:
    """Keyword gate, then reverse insertion order. Nothing else moves a hit."""

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options
        self.rows: list[dict] = []
        self._seq = 0

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def _put(self, record: object) -> None:
        row = _row(record, self._seq)
        self._seq += 1
        self.rows = [
            item
            for item in self.rows
            if not (item["path"] == row["path"] and item["chunk_index"] == row["chunk_index"])
        ]
        self.rows.append(row)

    async def upsert_chunks(self, records: list) -> None:
        for record in records or []:
            self._put(record)

    async def replace_file_chunks(self, path: str, records: list) -> None:
        await self.delete_file_chunks(path)
        await self.upsert_chunks(records)

    async def delete_file_chunks(self, path: str) -> None:
        self.rows = [item for item in self.rows if item["path"] != path]

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        self.rows = [
            item
            for item in self.rows
            if not (item["path"] == path and item["chunk_index"] == int(chunk_index))
        ]

    async def search(self, query: str, **kwargs):
        del kwargs
        hits = [item for item in self.rows if _matches(query, item["content"])]
        hits.sort(key=lambda item: item["seq"], reverse=True)
        return hits

    async def nearest(self, text: str, *, top_k: int = 3) -> list:
        query = _vector(text or "")
        scored = [(_cosine(query, item["vector"]), item) for item in self.rows]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {"path": item["path"], "content": item["content"], "cos": round(score, 6)}
            for score, item in scored[:top_k]
        ]

    async def list_chunks(self) -> list:
        return [
            {"path": item["path"], "chunk_index": item["chunk_index"], "content": item["content"]}
            for item in self.rows
        ]

    async def stats(self) -> dict:
        return {"backend": "recency", "total_chunks": len(self.rows), "policy": "newest"}
