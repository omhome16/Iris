"""The CLI's chrome — panels, status marks, tables and the start screen.

Every command used to draw its own layout out of `[iris.title]` markup and a bare
`rich.Table`. That produced six different header styles and no shared idea of what
a *finding* looks like. This module is that idea, in one place:

- a **header panel** says what the command is before it says what it found;
- a **section rule** separates the parts of a long report;
- a **status line** is the one shape a result takes everywhere — a mark, the word
  (`ok`/`warn`/`fail`), the subject, then the finding (`iris doctor`, `iris init`);
- a **table** is quiet: no vertical bars, a brand header, dim borders.

Two rules this module keeps:

**Colour never carries meaning alone.** Each level has a mark and a word, so a
piped log, a `NO_COLOR` shell and a screenshot all still read. The tests assert the
words rather than the styling for exactly this reason.

**Every character is cp1252-safe.** A Windows console falls back to cp1252, and a
character outside it raises `UnicodeEncodeError` instead of drawing a box — `↑`
did exactly that once. The marks here are `•`, `!` and `x`; the separators are
`·` and `›`; rich draws its own borders and knows how to downgrade those.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from iris_ai.cli.help_theme import LEVEL_STYLE

#: The one shape a result takes. All three are ASCII on purpose: this is the
#: leading character of every pasted-into-an-issue diagnostic line, so it has to
#: survive a codepage the console chose rather than one we picked. (A `↑` here
#: once ended a command with a traceback instead of printing a table.)
MARK = {"ok": "+", "warn": "!", "fail": "x"}

BULLET = "•"
CHEVRON = "›"
DOT = "·"

#: Quiet tables: a header line and no vertical rules. The opencode/hyperfine-ish
#: look — the data is the point, the frame is not.
TABLE_BOX = box.SIMPLE_HEAD
#: Panels get a rounded frame, which reads as "a summary" rather than "a table".
PANEL_BOX = box.ROUNDED


def frame(out: Console, kind: str = "table") -> box.Box:
    """The frame to draw with: designed on a terminal, portable everywhere else.

    Rich's box characters are not cp1252, and rich only downgrades them for a
    legacy Windows console — not for a pipe, a `tee`, a CI log or a captured
    test run. `tests/test_guards_cli.py` asserts a command's captured output
    still encodes to cp1252, because that is what `iris guards > guards.txt`
    does on Windows. So a non-terminal gets the ASCII frame, and the pretty one
    is reserved for the place a person is actually looking.
    """
    if out.is_terminal:
        return TABLE_BOX if kind == "table" else PANEL_BOX
    return box.ASCII


def header(out: Console, title: str, subtitle: str = "") -> None:
    """The command's identity: a brand-titled panel, before any findings."""
    body = Text()
    body.append(title, style="iris.brand")
    if subtitle:
        body.append("\n")
        body.append(subtitle, style="iris.sub")
    out.print(
        Panel(
            body,
            box=frame(out, "panel"),
            border_style="iris.box",
            padding=(0, 2),
            expand=False,
        )
    )


def section(out: Console, label: str) -> None:
    """A left-aligned rule with a dim label — the parts of a long report."""
    out.print(
        Rule(
            Text(label, style="iris.sub"),
            style="iris.rule",
            align="left",
            characters="—" if out.is_terminal else "-",
        )
    )


def status(out: Console, level: str, name: str, detail: str = "", *, indent: int = 2) -> None:
    """One finding: mark, word, subject, then the detail.

    The word is kept even when the mark and the colour are there, because those
    two are the first things a pipe or a `NO_COLOR` shell takes away.
    """
    style = LEVEL_STYLE.get(level, "")
    line = Text(" " * indent)
    line.append(MARK.get(level, DOT), style=style)
    line.append(" ")
    line.append(f"{level:4}", style=style)
    line.append(" ")
    line.append(name, style="bold")
    if detail:
        line.append(": ", style="iris.box")
        line.append(detail, style="iris.sub")
    out.print(line)


def counts(out: Console, levels: Iterable[str]) -> None:
    """The summary line: how many of each, in the same order every time."""
    tally = dict.fromkeys(("ok", "warn", "fail"), 0)
    for level in levels:
        tally[level] = tally.get(level, 0) + 1
    line = Text(" " * 2)
    line.append(str(sum(tally.values())), style="iris.num")
    line.append(" checks", style="iris.sub")
    for level in ("ok", "warn", "fail"):
        line.append(f"  {DOT}  ", style="iris.box")
        line.append(f"{level} ", style=LEVEL_STYLE[level])
        line.append(str(tally[level]), style="bold")
    out.print(line)


def grid(out: Console, rows: Sequence[tuple[str, str]], *, indent: int = 2) -> None:
    """Aligned `key  value` rows — the shape a small factual block takes.

    The indent is a real first column rather than `rich.padding.Padding`: Padding
    renders its child at the full console width, which turns a two-word block into
    a line of trailing spaces.
    """
    table = Table.grid(padding=(0, 2))
    table.add_column(width=max(0, indent))
    table.add_column(style="iris.key", justify="right")
    table.add_column()
    for key, value in rows:
        table.add_row("", str(key), value)
    out.print(table)


def table(
    out: Console,
    title: str,
    columns: Sequence[str | tuple[str, dict[str, Any]]],
    **kwargs: Any,
) -> Table:
    """A configured table: brand header, quiet frame.

    A column is either a name, or `(name, add_column_kwargs)` when it needs a
    justification or a style.
    """
    built = Table(
        title=title,
        title_style="iris.brand",
        title_justify="left",
        header_style="iris.brand",
        border_style="iris.box",
        box=frame(out),
        pad_edge=False,
        # Rich's default cell padding. Cells are separated by the border style and
        # the header, not by air: an extra space per side pushed a seven-column
        # table past 80 columns, and a folded cell breaks `grep` as well as the eye.
        padding=(0, 1),
        **kwargs,
    )
    for column in columns:
        name, options = (column, {}) if isinstance(column, str) else column
        built.add_column(name, **options)
    return built


def bullets(out: Console, items: Iterable[str], *, indent: int = 2) -> None:
    """A `•` list, dim mark, plain text."""
    for item in items:
        line = Text(" " * indent)
        line.append(f"{BULLET} ", style="iris.mark")
        line.append(item)
        out.print(line)


def steps(out: Console, title: str, items: Sequence[tuple[str, str]], *, subtitle: str = "") -> None:
    """A panel of `command — why`, which is how a command hands over to the next."""
    body = Text()
    if subtitle:
        body.append(subtitle + "\n", style="iris.sub")
    for index, (command, why) in enumerate(items):
        if index:
            body.append("\n")
        body.append("  " + CHEVRON + " ", style="iris.mark")
        body.append(command, style="iris.cmd")
        if why:
            body.append("  " + why, style="iris.sub")
    out.print(
        Panel(
            body,
            title=Text(title, style="iris.brand"),
            title_align="left",
            box=frame(out, "panel"),
            border_style="iris.box",
            padding=(0, 1),
            expand=False,
        )
    )


def note(out: Console, text: str, *, indent: int = 2) -> None:
    """Prose the reader needs but did not ask for (dim, never decoration)."""
    out.print(Text(" " * indent + text, style="iris.sub"))


def hint(out: Console, text: str, *, indent: int = 2) -> None:
    out.print(Text(" " * indent + text, style="iris.hint"))


def warn(out: Console, text: str) -> None:
    out.print(f"[iris.warn]{text}[/iris.warn]")


def error(out: Console, text: str) -> None:
    out.print(f"[iris.fail]{text}[/iris.fail]")


def failed(out: Console, label: str, detail: str) -> None:
    """`label: detail`, with the label carrying the colour — the error idiom."""
    out.print(f"[iris.fail]{label}[/iris.fail] {detail}")


def stack(*items: Any) -> Group:
    """Several renderables as one, for a caller that wants them inside a panel."""
    return Group(*items)


__all__ = [
    "BULLET",
    "CHEVRON",
    "DOT",
    "MARK",
    "bullets",
    "counts",
    "error",
    "failed",
    "frame",
    "grid",
    "header",
    "hint",
    "note",
    "section",
    "stack",
    "status",
    "steps",
    "table",
    "warn",
]
