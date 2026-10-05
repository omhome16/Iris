"""`iris core propose` — a git worktree and a report. Never an apply."""

from __future__ import annotations

from pathlib import Path

from iris_ai.cli.help_theme import console
from iris_ai.core.propose import propose


def run(action: str, request: str) -> int:
    out = console()
    if action != "propose":
        out.print("usage: iris core propose <request>")
        return 2
    if not request.strip():
        out.print("usage: iris core propose <request>")
        return 2
    repo = Path.cwd()
    dest = repo / ".proposals" / "next"
    if dest.exists():
        out.print(f"{dest} already exists; remove it or rename it before another proposal")
        return 2
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = propose(request, repo=repo, dest=dest)
    except (RuntimeError, FileExistsError) as exc:
        out.print(str(exc))
        return 1
    out.print(f"wrote {result['path']}")
    out.print("applied: false")
    return 0
