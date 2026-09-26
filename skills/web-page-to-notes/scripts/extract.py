"""Turn a local HTML file into readable notes.

    python extract.py <input.html> [output.md]

Standard library only, offline by design: this runs under the skill boundary in
`iris.skills.runner`, which hands it a minimal environment (no API keys, no
network credentials) and kills it if it overstays its timeout. Nothing here
reads the environment, opens a socket, or spawns a process — the skill's
`allowed-tools` are the agent's surface, and this script's surface is a file.

Output: markdown-ish notes — a one-line summary, headings kept as headings,
paragraphs as lines — next to the input file unless an output path is given.
"""

from __future__ import annotations

import re
import sys
from html.parser import HTMLParser
from pathlib import Path

_SKIP = {"script", "style", "noscript", "template", "svg"}
_BLOCK = {"p", "div", "section", "article", "li", "br", "tr", "blockquote", "pre"}
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3, "h4": 4, "h5": 5, "h6": 6}
_MAX_LINE = 400


class _Reader(HTMLParser):
    """Collect visible text, keeping the block/heading structure that makes it
    readable. Deliberately not a full HTML parser: the goal is notes, not DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True
        elif tag in _HEADINGS:
            self.parts.append(f"\n\n{'#' * _HEADINGS[tag]} ")
        elif tag in _BLOCK:
            self.parts.append("\n")
        elif tag == "li":
            self.parts.append("\n- ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in _BLOCK or tag in _HEADINGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        text = re.sub(r"\s+", " ", data).strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        self.parts.append(text + " ")


def to_notes(html: str) -> tuple[str, str]:
    reader = _Reader()
    reader.feed(html)
    reader.close()

    lines: list[str] = []
    for raw in "".join(reader.parts).splitlines():
        line = raw.rstrip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        line = line[:_MAX_LINE]
        if lines and lines[-1] == line:  # navigation menus repeat themselves
            continue
        lines.append(line)
    body = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return reader.title, body


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: extract.py <input.html> [output.md]")
        return 2
    source = Path(argv[1])
    if not source.is_file():
        print(f"no such file: {source}")
        return 1
    html = source.read_text(encoding="utf-8", errors="replace")
    title, body = to_notes(html)
    if not body:
        print("extracted no text — the page is probably rendered by JavaScript")
        return 1

    header = f"# {title}\n\n" if title else ""
    destination = Path(argv[2]) if len(argv) > 2 else source.with_suffix(".md")
    destination.write_text(header + body + "\n", encoding="utf-8")
    print(f"wrote {destination} ({len(body)} chars, title: {title or '(none)'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
