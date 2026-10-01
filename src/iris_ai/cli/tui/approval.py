"""Allow or deny a paused tool call."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ApprovalScreen(ModalScreen[str]):
    """Returns 'approved' or 'denied'."""

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        yield Static(self.question, id="question")
        with Horizontal():
            yield Button("Allow", id="allow", variant="success")
            yield Button("Deny", id="deny", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss("approved" if event.button.id == "allow" else "denied")
