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
import ipaddress
import re
import socket
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

from iris.config import settings

MAX_FETCH_CHARS = 200_000
MAX_REDIRECTS = 5
UA = "IrisAssistant/1.0 (personal memory assistant)"


class SSRFError(RuntimeError):
    """A URL resolved to a private/loopback/link-local address or an
    internal service hostname — refused before any request is made."""


# Internal Docker service hostnames the compose stack uses. Iris must never
# reach them through the ingest tool, even if they resolve publicly someday.
_BLOCKED_HOSTNAMES = {"localhost", "postgres", "iris-core", "telegram-mcp", "dashboard"}


def _is_blocked_ip(ip: ipaddress._BaseAddress) -> bool:
    """True for any address Iris must never fetch: loopback, RFC1918,
    link-local, CGNAT, reserved, multicast, unspecified."""
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def validate_url(url: str) -> None:
    """SSRF gate: scheme check, hostname allow/deny, DNS resolution with a
    private-IP reject. Raises SSRFError with a friendly tool message."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFError("only http(s) URLs are supported")
    host = (parsed.hostname or "").casefold()
    if not host:
        raise SSRFError("that URL is not reachable")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".local"):
        raise SSRFError("that URL is not reachable")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        raise SSRFError("that URL is not reachable")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if _is_blocked_ip(ip):
            raise SSRFError("that URL is not reachable")


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
    """Fetch a URL and return readable text (html or plain).

    SSRF-safe: the host is resolved and validated before the request, and
    every redirect hop is re-validated before it is followed.
    """
    validate_url(url)
    current = url
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(20.0, connect=10.0),
        follow_redirects=False,
        headers={"User-Agent": UA},
    ) as client:
        for _ in range(MAX_REDIRECTS + 1):
            resp = await client.get(current)
            if resp.status_code in (301, 302, 303, 307, 308):
                loc = resp.headers.get("location")
                if not loc:
                    break
                current = str(resp.url.join(loc))
                validate_url(current)  # re-gate every hop — redirects can pivot to internal hosts
                continue
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "").lower()
            if "html" in ctype:
                return html_to_text(resp.text)[:MAX_FETCH_CHARS]
            return resp.text[:MAX_FETCH_CHARS]
    raise SSRFError("too many redirects")


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