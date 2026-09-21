"""Ingestion tests — web search + URL import.

Live network is never hit: fetch_text is stubbed; the trust-model properties
(tier_for, never-promotable origin) are tested against the real code.
"""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from iris.ingest import SSRFError, fetch_text, html_to_text, import_path, ingest_url, validate_url
from iris.memory.files import WorkspaceFiles
from iris.memory.indexer import Reindexer
from iris.memory.provenance import Origin


def test_html_to_text_strips_markup_and_keeps_content():
    html = """
    <html><head><title>x</title></head><body>
    <script>var pwned = true;</script>
    <h1>Hello <b>World</b></h1>
    <p>First paragraph.</p><p>Second&nbsp;one &amp; done.</p>
    </body></html>
    """
    text = html_to_text(html)
    assert "Hello World" in text
    assert "First paragraph." in text
    assert "Second one & done." in text
    assert "pwned" not in text
    assert "<" not in text


def test_import_path_is_dated_and_deterministic(tmp_path: Path):
    a = import_path(tmp_path, "https://example.com/article")
    b = import_path(tmp_path, "https://example.com/article")
    assert a == b
    assert a.name.endswith(".md")
    assert "imports" in a.parts
    assert a.parent.parent == tmp_path


async def test_ingest_url_writes_untrusted_note(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch(url: str) -> str:
        assert url == "https://example.com/notes"
        return "The moon is made of green cheese. (A test fact.)"

    monkeypatch.setattr("iris.ingest.fetch_text", fake_fetch)
    files = WorkspaceFiles(tmp_path)

    result = await ingest_url(files.root, "https://example.com/notes")

    note = files.root / result["path"]
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "The moon is made of green cheese" in content
    assert "UNTRUSTED origin" in content

    reindexer = Reindexer(files, None)  # type: ignore[arg-type]
    origin, evergreen = reindexer.tier_for(result["path"])
    assert origin is Origin.UNTRUSTED
    assert evergreen is False  # imports decay; never promoted, never curated


async def test_ingest_url_rejects_empty_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    async def fake_fetch(url: str) -> str:
        return "   \n  "

    monkeypatch.setattr("iris.ingest.fetch_text", fake_fetch)
    files = WorkspaceFiles(tmp_path)
    with pytest.raises(RuntimeError, match="no readable text"):
        await ingest_url(files.root, "https://example.com/empty")


def test_untrusted_is_not_promotable():
    assert Origin.UNTRUSTED.promotable is False


# ── SSRF gate ────────────────────────────────────────────────────────────────

def _public_addrinfo(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def _private_addrinfo(host, port, *args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))]


def test_validate_url_rejects_private_and_internal_hosts(monkeypatch: pytest.MonkeyPatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", _private_addrinfo)
    for bad in [
        "http://127.0.0.1:8000/health",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/admin",
        "http://192.168.1.1/x",
        "http://[::1]:8000/x",
        "http://[fc00::1]/x",
        "http://postgres:5432/x",
        "http://iris-core:8000/x",
        "http://localhost:8000/x",
        "http://telegram-mcp:8100/mcp",
        "http://dashboard:8080/x",
    ]:
        with pytest.raises(SSRFError, match="not reachable"):
            validate_url(bad)
    with pytest.raises(SSRFError):
        validate_url("ftp://example.com/file")


def test_validate_url_accepts_public_host(monkeypatch: pytest.MonkeyPatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", _public_addrinfo)
    validate_url("https://example.com/article")  # must not raise


async def test_fetch_text_rejects_private_resolution(monkeypatch: pytest.MonkeyPatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", _private_addrinfo)
    with pytest.raises(SSRFError, match="not reachable"):
        await fetch_text("http://example.com/article")


async def test_fetch_text_regates_redirect_hops(monkeypatch: pytest.MonkeyPatch):
    """A redirect to an internal address must be caught, not followed."""
    import socket

    def resolver(host, port, *args, **kwargs):
        # literal private addresses resolve to themselves; everything else is public
        ip = host if host in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "169.254.169.254") else "93.184.216.34"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolver)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, text="pwned")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "iris.ingest.httpx.AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler), **k),
    )
    with pytest.raises(SSRFError, match="not reachable"):
        await fetch_text("https://example.com/start")


async def test_fetch_text_returns_public_page(monkeypatch: pytest.MonkeyPatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", _public_addrinfo)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<h1>Hello world</h1>", headers={"content-type": "text/html"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "iris.ingest.httpx.AsyncClient",
        lambda *a, **k: real_client(transport=httpx.MockTransport(handler), **k),
    )
    assert "Hello world" in await fetch_text("https://example.com/start")
