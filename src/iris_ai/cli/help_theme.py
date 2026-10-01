"""Rich styling for the CLI. Plain output when piped or NO_COLOR is set.

One palette, defined once, used by every command. It is the banner's dawn ramp —
violet to rose to amber (`art.STOPS`) — sampled into a handful of *semantic*
names, so a command says what a thing *is* (`iris.fail`) and never which colour
it should be. Restyling the CLI is then an edit to this file rather than a hunt
through fifteen modules.

Two rules the palette exists to enforce:

- **Nothing important is carried by colour alone.** Every level also has a word
  (`ok`, `warn`, `fail`) and a mark, because a piped log, a `NO_COLOR` shell and a
  screenshot in a bug report all lose the colour. Tests assert the words.
- **Plain when it is not a terminal.** Rich strips styles for a pipe by itself, so
  `iris doctor > doctor.txt` is readable text; `console()` adds nothing that would
  defeat that.

The characters `ui.py` draws with are cp1252-safe on purpose: a Windows console
falls back to cp1252 and a character outside it raises `UnicodeEncodeError`
rather than rendering a box (`tests/test_cli.py` asserts this over every literal
in this package). Rich owns its own border characters and downgrades those
itself; what it cannot downgrade is a character *we* wrote.
"""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

#: The dawn ramp, as hex — the same stops `art.STOPS` interpolates between.
BRAND = "#b0578f"
BRAND_BRIGHT = "#e87a63"
ACCENT = "#ffc46b"
COOL = "#5aa9c9"
OK = "#5fbf7f"
WARN = "#e0a44a"
FAIL = "#e0586b"
MUTED = "grey50"

THEME = Theme(
    {
        # identity
        "iris.brand": f"bold {BRAND}",
        "iris.title": f"bold {BRAND}",
        "iris.mark": f"bold {BRAND_BRIGHT}",
        "iris.accent": f"bold {ACCENT}",
        # structure
        "iris.rule": BRAND,
        "iris.box": MUTED,
        "iris.sub": MUTED,
        "iris.key": COOL,
        "iris.cmd": f"bold {COOL}",
        "iris.flag": COOL,
        "iris.arg": ACCENT,
        "iris.num": f"bold {ACCENT}",
        # outcome — the three words the whole CLI shares
        "iris.ok": f"bold {OK}",
        "iris.warn": f"bold {WARN}",
        "iris.fail": f"bold {FAIL}",
        "iris.hint": f"italic {MUTED}",
    }
)


def console(stderr: bool = False) -> Console:
    """The one console every command prints through.

    `highlight=False` keeps rich's repr highlighter from restyling numbers and
    paths inside prose, and `emoji=False` keeps `:name:` sequences literal — a
    designed screen should render exactly what the code wrote.
    """
    return Console(theme=THEME, stderr=stderr, highlight=False, emoji=False)


LEVEL_STYLE = {"ok": "iris.ok", "warn": "iris.warn", "fail": "iris.fail", "opt": "iris.sub"}
