"""Onboarding — the birth of Iris's identity.

First-run wizard (OpenClaw-style): name → personality → tone → timezone →
sleep preference. Progress persists in `workspace/config/iris.json`; the
completed profile is written to USER.md with owner provenance.

Fresh-start guarantee: after `scripts/fresh_start.py` (or a wiped workspace),
`onboarded` is False and the chat graph routes every message through the
wizard until it completes — Iris starts with *zero memory but instructions
intact* (AGENTS.md is never touched).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from iris.memory.files import WorkspaceFiles


@dataclass(slots=True)
class OnboardingState:
    onboarded: bool = False
    step: int = 0
    asked: bool = False  # whether the current question has been asked yet
    owner_name: str = ""
    personality: str = ""
    tone: str = ""
    timezone: str = ""
    sleep_pref: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2) + "\n"


STEPS = [
    ("owner_name", "What should I call you?"),
    (
        "personality",
        "How would you like me to be? (e.g. warm and curious, dry and efficient, playful)",
    ),
    (
        "tone",
        "Tone for messages? (e.g. short and direct, friendly and detailed)",
    ),
    ("timezone", "Your timezone? (e.g. Asia/Kolkata, UTC, America/New_York)"),
    (
        "sleep_pref",
        "When should I run my nightly dream consolidation? (hour 0-23, e.g. 4)",
    ),
]


class OnboardingWizard:
    def __init__(self, files: WorkspaceFiles) -> None:
        self.files = files
        self.config_path = files.config_file()
        self.state = self._load()

    def _load(self) -> OnboardingState:
        if not self.config_path.exists():
            return OnboardingState()
        try:
            return OnboardingState(**json.loads(self.config_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, TypeError):
            return OnboardingState()

    def save(self) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(self.state.to_json(), encoding="utf-8")

    @property
    def onboarded(self) -> bool:
        return self.state.onboarded

    def current_prompt(self) -> str:
        """What to ask next, or the completion greeting."""
        if self.state.onboarded:
            return f"Welcome back, {self.state.owner_name or 'friend'}."
        if self.state.step >= len(STEPS):
            return self._finish()
        return STEPS[self.state.step][1]

    def apply_answer(self, answer: str) -> str:
        """Record the wizard's next answer; returns the next prompt."""
        if self.state.onboarded:
            return self.current_prompt()
        if self.state.step >= len(STEPS):
            return self._finish()
        field_name, _ = STEPS[self.state.step]
        setattr(self.state, field_name, answer.strip())
        self.state.step += 1
        self.state.asked = True  # waiting for the next answer already
        self.save()
        if self.state.step >= len(STEPS):
            return self._finish()
        return STEPS[self.state.step][1]

    def greet(self) -> str:
        """Ask the current question (once). The *next* message is the answer."""
        if self.state.onboarded:
            return self.current_prompt()
        if not self.state.asked:
            self.state.asked = True
            self.save()
        return self.current_prompt()

    def _finish(self) -> str:
        self._write_user_profile()
        self.state.onboarded = True
        self.save()
        name = self.state.owner_name or "friend"
        return (
            f"Done — I'm yours, {name}. Your profile is written to USER.md "
            "(owner provenance), and my dream cycle is set for hour "
            f"{self.state.sleep_pref or 4}. What's on your mind?"
        )

    def _write_user_profile(self) -> None:
        lines = [
            "# USER.md — the owner's profile",
            "",
            "> Written during onboarding. Owner provenance. Refined by dreaming.",
            "",
            f"- Name: {self.state.owner_name or '(not given)'}",
            f"- Personality preference: {self.state.personality or '(not given)'}",
            f"- Message tone: {self.state.tone or '(not given)'}",
            f"- Timezone: {self.state.timezone or 'UTC'}",
            f"- Dream consolidation hour: {self.state.sleep_pref or '4'}",
            f"- Onboarded: {datetime.now().isoformat(timespec='seconds')}",
            "",
        ]
        self.files.write_curated(self.files.user, "\n".join(lines))