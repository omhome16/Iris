"""A memory-backend change is a rebuild from Markdown, not a copy of the index.

`iris migrate` already rebuilds. This module states the contract and refuses
to pretend a second index was swapped into place.
"""

from __future__ import annotations

from pathlib import Path


def workspace_ready(root: Path) -> list[str]:
    """Problems that would make a rebuild have nothing to read."""
    if not root.is_dir():
        return ["workspace is missing"]
    if (root / "MEMORY.md").is_file() or (root / "memory").is_dir():
        return []
    return ["no MEMORY.md or memory/ to rebuild from"]


def swap_plan(current: str, target: str, *, root: Path | None = None) -> dict[str, object]:
    """Describe the swap. Nothing is written."""
    problems = workspace_ready(root) if root is not None else []
    return {
        "from": current,
        "to": target,
        "source": "workspace markdown",
        "method": "rebuild",
        "command": f"iris migrate --to {target}",
        "problems": problems,
        "applied": False,
    }
