"""First-run setup and later edits. No persona until the owner writes one.

`iris init --yes` writes a neutral profile without questions, which is what CI
uses. `iris config` opens the same screen again.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.onboarding import OnboardingWizard


def apply_setup(
    root: Path,
    *,
    owner_name: str = "",
    assistant_name: str = "assistant",
    tone: str = "",
    timezone: str = "UTC",
    sleep_hour: str = "4",
    persona: str = "",
) -> None:
    files = WorkspaceFiles(root)
    OnboardingWizard(files, None).configure(
        owner_name=owner_name,
        assistant_name=assistant_name or "assistant",
        tone=tone,
        timezone=timezone or "UTC",
        sleep_hour=sleep_hour or "4",
        persona=persona,
    )


def run_config(*, yes: bool = False) -> int:
    root = Path(settings.workspace_dir)
    if yes:
        apply_setup(root)
        return 0
    from iris_ai.cli.tui.setup import run_setup

    return run_setup(root)
