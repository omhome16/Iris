"""A proposal for a core change, in its own git worktree.

The worktree gets a PROPOSAL.md. Nothing is copied back onto the running
tree, and the protected paths are not edited.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

PROTECTED = (
    "src/iris_ai/approval.py",
    "src/iris_ai/kernel/pause.py",
    "src/iris_ai/lifecycle/",
    "src/iris_ai/isolation/",
    "src/iris_ai/artifacts/",
)


def propose(request: str, *, repo: Path, dest: Path) -> dict[str, object]:
    """Create `dest` as a detached worktree and write the proposal there."""
    dest = dest.resolve()
    if dest.exists():
        raise FileExistsError(dest)
    completed = subprocess.run(
        ["git", "worktree", "add", "--detach", str(dest), "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "git worktree failed").strip()
        raise RuntimeError(detail)
    named = [path for path in PROTECTED if path.rstrip("/") in request]
    lines = [
        "# Proposal",
        "",
        request.strip(),
        "",
        "This worktree is a proposal. Iris did not apply it.",
        "Protected paths stay untouched:",
        *[f"- {path}" for path in PROTECTED],
        "",
    ]
    if named:
        lines.append("The request names a protected path. It was not edited.")
        lines.append("")
    (dest / "PROPOSAL.md").write_text("\n".join(lines), encoding="utf-8")
    return {"path": str(dest), "applied": False, "protected": list(PROTECTED), "named": named}
