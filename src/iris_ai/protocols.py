"""The pieces of the harness. Swap any of them from config/harness.toml.

A value in `[components]` is either a built-in name (`default`, `sqlite`,
`off`) or `package.module:Class`. The class is constructed with the runtime.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ContextBuilder(Protocol):
    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        """Return the prompt prefix and the skill names it mentioned."""


@runtime_checkable
class Capture(Protocol):
    async def maybe_capture(self, *, user_message: str, reply: str, known_context: str) -> str:
        """Return a one-line note of what was stored, or '' when nothing was."""


@runtime_checkable
class Consolidator(Protocol):
    async def sleep(self):
        """Fold recent notes into long-term memory. Return a record."""


@runtime_checkable
class PersonaSource(Protocol):
    def text(self) -> str:
        """The system prompt for this turn, including any owner-written persona."""
