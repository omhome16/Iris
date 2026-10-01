"""The setup screen. Collects a profile and writes it. No default persona."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Static

from iris_ai.cli.setup import apply_setup


class SetupScreen(Screen[bool]):
    """The same profile form, pushed inside an already-running chat."""

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    def compose(self) -> ComposeResult:
        yield Static("Set up this workspace. Leave persona empty to stay neutral.", id="status")
        with Vertical():
            yield Label("Your name")
            yield Input(placeholder="what should the assistant call you", id="owner")
            yield Label("Assistant name")
            yield Input(value="assistant", id="assistant")
            yield Label("Tone (optional)")
            yield Input(placeholder="warm, short, formal…", id="tone")
            yield Label("Timezone")
            yield Input(value="UTC", id="timezone")
            yield Label("Consolidation hour (0-23, or off)")
            yield Input(value="4", id="sleep")
            yield Label("Persona (optional, written to PERSONA.md)")
            yield Input(placeholder="leave blank for no persona", id="persona")
            yield Button("Save", id="save")
            yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(False)
            return
        if event.button.id != "save":
            return
        apply_setup(
            self.root,
            owner_name=self.query_one("#owner", Input).value,
            assistant_name=self.query_one("#assistant", Input).value,
            tone=self.query_one("#tone", Input).value,
            timezone=self.query_one("#timezone", Input).value or "UTC",
            sleep_hour=self.query_one("#sleep", Input).value or "4",
            persona=self.query_one("#persona", Input).value,
        )
        self.dismiss(True)


class SetupApp(App):
    CSS_PATH = "theme.tcss"
    TITLE = "Iris setup"

    def __init__(self, root: Path) -> None:
        super().__init__()
        self.root = root

    def compose(self) -> ComposeResult:
        yield Static("Set up this workspace. Leave persona empty to stay neutral.", id="status")
        with Vertical():
            yield Label("Your name")
            yield Input(placeholder="what should the assistant call you", id="owner")
            yield Label("Assistant name")
            yield Input(value="assistant", id="assistant")
            yield Label("Tone (optional)")
            yield Input(placeholder="warm, short, formal…", id="tone")
            yield Label("Timezone")
            yield Input(value="UTC", id="timezone")
            yield Label("Consolidation hour (0-23, or off)")
            yield Input(value="4", id="sleep")
            yield Label("Persona (optional, written to PERSONA.md)")
            yield Input(placeholder="leave blank for no persona", id="persona")
            yield Button("Save", id="save")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "save":
            return
        apply_setup(
            self.root,
            owner_name=self.query_one("#owner", Input).value,
            assistant_name=self.query_one("#assistant", Input).value,
            tone=self.query_one("#tone", Input).value,
            timezone=self.query_one("#timezone", Input).value or "UTC",
            sleep_hour=self.query_one("#sleep", Input).value or "4",
            persona=self.query_one("#persona", Input).value,
        )
        self.exit(0)


def run_setup(root: Path) -> int:
    app = SetupApp(root)
    app.run()
    return 0
