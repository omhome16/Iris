"""`iris version` — version, interpreter, install location.

`version_lines()` stays the plain three-line form (`iris <v>`, `python <v>`,
`package <path>`), because the start screen and the tests read it as data. This
module only decides how it is *drawn*: a brand panel for the version, then the two
facts that explain a bug report — which interpreter, and which install.
"""

from __future__ import annotations

import sys
from pathlib import Path

import iris_ai


def _package_location() -> Path:
    return Path(iris_ai.__file__).resolve().parent


def version_lines() -> list[str]:
    return [
        f"iris {iris_ai.__version__}",
        f"python {sys.version.split()[0]}",
        f"package  {_package_location()}",
    ]


def print_version() -> None:
    from iris_ai.cli import ui
    from iris_ai.cli.help_theme import console

    out = console()
    ui.header(out, f"iris {iris_ai.__version__}", "a personal agent harness: library, CLI, API, editor")
    ui.grid(
        out,
        [
            ("python", sys.version.split()[0]),
            ("package", str(_package_location())),
        ],
    )
