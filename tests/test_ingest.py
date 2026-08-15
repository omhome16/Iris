"""Ingestion tests — web search + URL import.

Live network is never hit: fetch_text is stubbed; the trust-model properties
(tier_for, never-promotable origin) are tested against the real code.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iris.ingest import html_to_text, import_path, ingest_url
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