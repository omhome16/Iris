"""Ingestion — web search + URL/document ingestion.

The trust model keeps this safe by construction: anything ingested from the
web is indexed with UNTRUSTED origin, which can never be promoted into the
curated core (MEMORY.md / USER.md), no matter how relevant it becomes. It is
recallable on demand, never believed silently.

Web search uses Tavily (free tier ~1000 requests/month). Set TAVILY_API_KEY
in .env. Without a key, web_search returns a graceful "not configured" and
ingest_url still works (direct URL fetch needs no key).
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

import httpx

from iris.config import settings

MAX_FETCH_CHARS = 200_000
UA = "IrisAssistant/1.0 (personal memory assistant)"


def html_to_text(html: str) -> str:
    """Best-effort HTML → readable text. Not a full parser — good enough to
    turn articles and docs pages into indexable memory."""
    text = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<!--.*?-->", " ", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


async def fetch_text(url: str) -> str:
    """Fetch a URL and return readable text (html or plain)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": UA},
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        ctype = resp.headers.get("content-type", "").lower()
        if "html" in ctype:
            return html_to_text(resp.text)[:MAX_FETCH_CHARS]
        return resp.text[:MAX_FETCH_CHARS]


async def web_search(query: str, max_results: int = 5) -> list[dict]:
    """Tavily search — the only model-free way to search the web from a tool.
    Raises RuntimeError when TAVILY_API_KEY is not configured."""
    if not settings.tavily_api_key:
        raise RuntimeError(
            "web search is not configured — set TAVILY_API_KEY in .env "
            "(free tier at tavily.com)"
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
            for r in data.get("results", [])[:max_results]
        ]


def import_path(workspace: Path, url: str) -> Path:
    """Deterministic import file path: imports/YYYY-MM-DD-<urlhash>.md"""
    h = hashlib.sha1(url.encode()).hexdigest()[:8]
    return workspace / "imports" / f"{date.today().isoformat()}-{h}.md"


async def ingest_url(workspace: Path, url: str) -> dict:
    """Fetch a URL and store its text as an import note (UNTRUSTED origin,
    recallable, never promotable). Returns the written path + stats."""
    text = await fetch_text(url)
    if len(text.strip()) < 40:
        raise RuntimeError("page contained no readable text")
    path = import_path(workspace, url)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# Import: {url}\n\n> Fetched {date.today().isoformat()} · UNTRUSTED origin — "
        f"recallable on demand, never promoted into curated memory.\n\n{text}",
        encoding="utf-8",
        newline="\n",
    )
    return {"path": path.relative_to(workspace).as_posix(), "chars": len(text)}