"""The start screen: a drawn iris, generated rather than pasted.

`ascii-art.txt` files rot — they are hand-aligned, break at other terminal
widths, and nobody can tell a rendering bug from a typo. This module draws the
image from a small polar pattern instead, which buys three things:

- **it fits the terminal.** The grid is a function of width, so a 44-column
  window gets a compact mark rather than a wrap that ruins the picture;
- **it is symmetric by construction.** Every row is exactly `width` cells, so
  the art cannot drift out of alignment;
- **it is testable.** The tests assert row widths, determinism, and that nothing
  but space/ramp characters is emitted — properties, not golden files.

The pattern is an eye opening at dawn: a rosette iris (six petals) around a
dark pupil, a bright limbal ring, and rays that fade at the corners. Colour is a
vertical dawn ramp, quantised into runs so the console gets a handful of spans
per line instead of one per character.

Nothing here raises or imports anything heavy: the CLI must render on a broken
terminal, a pipe, or a `NO_COLOR` shell, and each of those is a code path with a
test behind it.
"""

from __future__ import annotations

import math
import os

from rich.console import Console
from rich.text import Text

#: Density ramp, darkest → brightest. ASCII on purpose: it renders identically
#: in Windows Terminal, a Linux TTY and a CI log, which block glyphs do not.
RAMP = " .:-=+*#%@"

#: The dawn ramp, dark violet → rose → amber. Same family as the palette the
#: old console used (`docs/console.md` is gone; the taste survived it).
STOPS = ("#1b1035", "#5b2a86", "#a84a80", "#e87a63", "#ffc46b")

WIDE = (76, 19)
COMPACT = (44, 11)

#: Cells whose brightness is below this are emitted as a space — the art sits on
#: the terminal's own background rather than in a rectangle.
_CUTOFF = 0.14


def _rgb(hex_colour: str) -> tuple[int, int, int]:
    value = hex_colour.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _ramp_colour(t: float) -> str:
    """Sample the dawn ramp at `t` in [0, 1], quantised to 24 levels.

    Quantisation is the point: neighbouring cells share a colour, so a line is
    emitted as a few spans. It also makes the output stable for tests.
    """
    steps = len(STOPS) - 1
    position = max(0.0, min(1.0, t)) * steps
    index = min(int(position), steps - 1)
    local = position - index
    # 24 levels over the whole ramp keeps runs long without banding visibly.
    local = round(local * 24) / 24
    left, right = _rgb(STOPS[index]), _rgb(STOPS[index + 1])
    mixed = tuple(round(a + (b - a) * local) for a, b in zip(left, right, strict=True))
    return "#{:02x}{:02x}{:02x}".format(*mixed)


def _intensity(x: float, y: float) -> float:
    """The pattern at one sample point, in [0, 1].

    `x` and `y` are both in [-1, 1] over the grid's width and height. The radius
    doubles `x`, which is the aspect correction: a cell is about twice as tall
    as it is wide, so `radius = 1` is a circle that touches the top and bottom
    edges rather than a lens. Everything below is expressed in that unit.

    Composition, outside in: rays leaving the eye, a lash line, a bright limbal
    ring, petal texture inside the iris, and a dark pupil with one highlight.
    """
    radius = math.hypot(2.0 * x, y)
    angle = math.atan2(y, 2.0 * x)

    # The almond that makes it read as an eye: widest at the centre, tapering
    # but never vanishing at the ends.
    lid = 0.20 + 0.72 * max(0.0, 1.0 - x * x) ** 0.7
    edge = math.exp(-(((abs(y) - lid) / 0.06) ** 2))
    inside = math.exp(-((abs(y) / lid) ** 8))
    outside = 1.0 - inside

    iris = math.exp(-((radius / 0.45) ** 4))  # 1 inside the iris, 0 far outside
    petals = 0.5 + 0.5 * math.cos(6.0 * angle)
    body = iris * (0.34 + 0.56 * petals) * (0.30 + 0.70 * min(1.0, radius / 0.34))
    limbal = math.exp(-(((radius - 0.42) / 0.045) ** 2))
    highlight = math.exp(-(((x + 0.12) ** 2) / 0.008 + ((y + 0.14) ** 2) / 0.003))
    # Rays belong outside the eye: inside is the sclera, and filling it with
    # texture is what makes a drawn eye look like static.
    rays = (abs(math.cos(5.0 * angle)) ** 10) * max(0.0, 1.0 - radius) * 0.36 * (1.0 - iris) * outside
    white = 0.05 * inside * (1.0 - iris)

    # The pupil is dark, with a soft edge so it is not a punched hole.
    pupil = 1.0 - 0.96 * math.exp(-((radius / 0.115) ** 4))

    return min(
        1.0,
        (0.62 * body + 0.95 * limbal + 0.85 * highlight + rays + white + 0.85 * edge) * pupil,
    )


def art_rows(width: int, height: int) -> list[str]:
    """The greyscale image: `height` rows of exactly `width` characters."""
    width = max(8, int(width))
    height = max(3, int(height))
    rows: list[str] = []
    for row in range(height):
        # Character cells are about twice as tall as they are wide, so the
        # vertical sample is doubled to keep the circle round.
        y = ((row + 0.5) / height) * 2.0 - 1.0
        cells: list[str] = []
        for column in range(width):
            x = ((column + 0.5) / width) * 2.0 - 1.0
            value = _intensity(x, y)
            cells.append(" " if value < _CUTOFF else RAMP[min(len(RAMP) - 1, int(value * len(RAMP)))])
        rows.append("".join(cells))
    return rows


def art_text(width: int, height: int, *, color: bool = True) -> Text:
    """The start-screen mark as rich text, coloured along the dawn ramp."""
    rows = art_rows(width, height)
    text = Text()
    for row_index, row in enumerate(rows):
        if not color:
            text.append(row + "\n")
            continue
        # Vertical position (0 top → 1 bottom) and radial warmth: light rises
        # from the bottom centre, which is what makes it read as a sunrise.
        vertical = row_index / max(1, len(rows) - 1)
        run: list[str] = []
        run_style = ""
        for column, char in enumerate(row):
            if char == " ":
                if run:
                    text.append("".join(run), style=run_style)
                    run = []
                text.append(" ")
                continue
            horizontal = abs((column + 0.5) / len(row) * 2.0 - 1.0)
            warmth = max(0.0, min(1.0, 0.35 + 0.65 * vertical - 0.45 * horizontal))
            style = _ramp_colour(warmth)
            if not run:
                run, run_style = [char], style
            elif style == run_style:
                run.append(char)
            else:
                text.append("".join(run), style=run_style)
                run, run_style = [char], style
        if run:
            text.append("".join(run), style=run_style)
        text.append("\n")
    return text


def size_for(console: Console) -> tuple[int, int]:
    """Which mark fits: the full eye, or the compact one."""
    width = console.width or 80
    return COMPACT if width < WIDE[0] + 4 else WIDE


def banner_enabled(console: Console, *, no_banner: bool = False) -> bool:
    """Should the start screen draw at all?

    Off for a pipe, a `NO_COLOR` shell, an explicit flag, and `IRIS_NO_BANNER`.
    A banner that draws itself into a log file is noise, and a start screen is
    decoration — never a dependency of the command that follows it.
    """
    if no_banner or os.environ.get("IRIS_NO_BANNER", "").strip() not in ("", "0"):
        return False
    if os.environ.get("NO_COLOR"):
        return False
    return bool(console.is_terminal)


def render_banner(console: Console, *, subtitle: str = "", color: bool = True) -> None:
    """Draw the mark and, optionally, a line under it."""
    width, height = size_for(console)
    console.print(art_text(width, height, color=color), end="")
    if subtitle:
        console.print(f"  [iris.title]{subtitle}[/iris.title]")


__all__ = [
    "COMPACT",
    "RAMP",
    "STOPS",
    "WIDE",
    "art_rows",
    "art_text",
    "banner_enabled",
    "render_banner",
    "size_for",
]
