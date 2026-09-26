"""Computer-use (P7) — the ability to act on a screen, fenced three ways.

Iris's design rule has always been *no host access*: memory, sandbox files and
the network through declared tools. Screen control is the first capability that
acts on something Iris does not own, so the audit's two latent weaknesses — an
undeclared tool surface and an unstated kernel boundary — stop being latent here.

The package is deliberately four small pieces, each of which can refuse:

- `actions` — the closed vocabulary (`screenshot`, `navigate`, `click`, `type`)
  and which of them are destructive.
- `provider` — the driver boundary. A missing driver is an `unavailable`
  *refusal*, never an `ImportError` in the middle of a turn.
- `permissions` — suffix-matched allowlists, confirmation for the destructive
  subset, and a per-session grant with an action budget so one approval cannot
  authorize an unbounded loop.
- `audit` — an append-only `config/actions.jsonl` that records what/where/outcome
  and **never** what was typed.

`session.Computer` composes them behind one `execute()` so every attempt —
refused, cancelled, budget-exhausted or performed — produces exactly one audit
record. Read `docs/deployment.md` and the P7 plan for the operational story.
"""

from __future__ import annotations

from iris_ai.computer.actions import Action, ActionKind, Observation
from iris_ai.computer.audit import ActionLog
from iris_ai.computer.permissions import PermissionModel
from iris_ai.computer.provider import (
    ComputerProvider,
    NullProvider,
    PlaywrightProvider,
    provider_from_settings,
)
from iris_ai.computer.session import Computer, computer_from_settings

__all__ = [
    "Action",
    "ActionKind",
    "ActionLog",
    "Computer",
    "ComputerProvider",
    "NullProvider",
    "Observation",
    "PermissionModel",
    "PlaywrightProvider",
    "computer_from_settings",
    "provider_from_settings",
]
