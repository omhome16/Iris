"""`iris version` — version, interpreter, install location."""

from __future__ import annotations

import sys
from pathlib import Path

import iris


def _package_location() -> Path:
    return Path(iris.__file__).resolve().parent


def version_lines() -> list[str]:
    return [
        f"iris {iris.__version__}",
        f"python {sys.version.split()[0]}",
        f"package  {_package_location()}",
    ]


def print_version() -> None:
    for line in version_lines():
        print(line)
