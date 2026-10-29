"""Graph memory. Chunks, plus typed links, plus a one-hop walk.

Keyword overlap picks the seeds. Each seed then contributes the chunks it
points at (`related`, `supports`, `supersedes`). Ranking stays on the keyword
score. Recency does not break a tie, so this store is not the recency floor
and not the fused scorer.
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


def _overlap(query: str, content: str) -> int:
    haystack = content.casefold()
    tokens = [token for token in (query or "").casefold().split() if len(token) >= 3]
    if not tokens:
        needle = (query or "").casefold().strip()
        return 1 if needle and needle in haystack else 0
    return sum(1 for token in tokens if token in haystack)


def _row(record: object, seq: int) -> dict:
    if isinstance(record, dict):
        content = str(record.get("content") or "")
        path = str(record.get("path") or "")
        index = int(record.get("chunk_index") or 0)
    else:
        content = str(getattr(record, "content", "") or "")
        path = str(getattr(record, "path", "") or "")
        index = int(getattr(record, "chunk_index", 0) or 0)
    return {
        "content": content,
        "path": path,
        "chunk_index": index,
        "seq": seq,
        "vector": _vector(content),
    }


class GraphMemory:
    """An edge table beside the chunks. Search walks one hop and then stops."""

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx
        self.options = options
        self.rows: list[dict] = []
        self.edges: list[tuple[tuple[str, int], tuple[str, int], str]] = []
        self._seq = 0

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        return None

    def link(self, src_path: str, src_index: int, dst_path: str, dst_index: int, rel: str) -> None:
        """Record one typed edge. Retrieval will cross it once."""
        self.edges.append(((src_path, int(src_index)), (dst_path, int(dst_index)), rel))

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
        self.edges = [edge for edge in self.edges if edge[0][0] != path and edge[1][0] != path]

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        key = (path, int(chunk_index))
        self.rows = [item for item in self.rows if (item["path"], item["chunk_index"]) != key]
        self.edges = [edge for edge in self.edges if edge[0] != key and edge[1] != key]

    def _by_key(self) -> dict[tuple[str, int], dict]:
        return {(item["path"], item["chunk_index"]): item for item in self.rows}

    async def search(self, query: str, **kwargs):
        del kwargs
        seeds = [item for item in self.rows if _overlap(query, item["content"]) > 0]
        seeds.sort(key=lambda item: (-_overlap(query, item["content"]), item["seq"]))
        found = {(item["path"], item["chunk_index"]) for item in seeds}
        table = self._by_key()
        hopped: list[dict] = []
        for seed in seeds:
            key = (seed["path"], seed["chunk_index"])
            for src, dst, rel in self.edges:
                if src != key or dst in found or dst not in table:
                    continue
                found.add(dst)
                neighbor = dict(table[dst])
                neighbor["via"] = rel
                hopped.append(neighbor)
        return [*seeds, *hopped]

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
        return {
            "backend": "graph",
            "total_chunks": len(self.rows),
            "edges": len(self.edges),
            "policy": "keyword-plus-one-hop",
        }
