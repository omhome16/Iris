"""Conformance checks for a local component folder."""

from __future__ import annotations

from pathlib import Path

from iris_ai.plug import check_folder


def check_component(path: str | Path) -> tuple[bool, str]:
    """Import `path` and confirm it implements its kind. Safe to call from tests."""
    return check_folder(Path(path))
