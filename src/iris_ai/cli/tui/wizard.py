"""One setup wizard: provider, key, model, embeddings, parts, MCP, profile."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Footer, Input, Label, Static

from iris_ai.cli.setup import apply_wizard
from iris_ai.components import list_options
from iris_ai.mcp.catalog import load_catalog
from iris_ai.providers import PROVIDERS

STEPS = ("provider", "key", "model", "embeddings", "parts", "mcp", "profile")


class WizardApp(App):
    CSS_PATH = Path(__file__).with_name("theme.tcss")
    TITLE = "Iris setup"

    def __init__(self, root: Path, *, start: str = "provider") -> None:
        super().__init__()
        self.root = root
        self.step = STEPS.index(start) if start in STEPS else 0
        self.answers: dict[str, str] = {
            "provider": "auto",
            "api_key": "",
            "model": "",
            "embeddings": "none",
            "context": "default",
            "memory": "sqlite",
            "persona": "file",
            "mcp": "",
            "owner": "",
            "assistant": "assistant",
            "tone": "",
            "timezone": "UTC",
            "sleep": "4",
            "new_kind": "",
            "new_name": "",
        }

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield Vertical(id="body")
        with Vertical(id="bottom"):
            yield Button("Back", id="back")
            yield Button("Next", id="next")
            yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._show(), exclusive=True)

    async def _show(self) -> None:
        name = STEPS[self.step]
        self.query_one("#status", Static).update(f"Step {self.step + 1} of {len(STEPS)}: {name}")
        body = self.query_one("#body", Vertical)
        await body.remove_children()
        if name == "provider":
            await body.mount(Label("Provider. Add 'any' for any LiteLLM model string."))
            await body.mount(Input(value=self.answers["provider"], id="provider"))
            await body.mount(Static(", ".join(PROVIDERS)))
        elif name == "key":
            await body.mount(Label("API key. It is saved to .env and never shown again."))
            await body.mount(Input(password=True, id="api_key", placeholder="leave blank to keep the current key"))
        elif name == "model":
            await body.mount(Label("Model id, or leave blank for the provider default."))
            await body.mount(Input(value=self.answers["model"], id="model", placeholder="provider/model"))
        elif name == "embeddings":
            await body.mount(Label("Embeddings: gemini, ollama, or none (keyword search)."))
            await body.mount(Input(value=self.answers["embeddings"], id="embeddings"))
        elif name == "parts":
            await body.mount(Label("context: " + ", ".join(list_options("context"))))
            await body.mount(Input(value=self.answers["context"], id="context"))
            await body.mount(Label("memory: " + ", ".join(list_options("memory"))))
            await body.mount(Input(value=self.answers["memory"], id="memory"))
            await body.mount(Label("persona: " + ", ".join(list_options("persona"))))
            await body.mount(Input(value=self.answers["persona"], id="persona"))
            await body.mount(Label("Create new: kind (context, memory, persona, role, channel) and name"))
            await body.mount(Input(placeholder="kind", id="new_kind"))
            await body.mount(Input(placeholder="name", id="new_name"))
        elif name == "mcp":
            names = ", ".join(load_catalog()) or "(no catalog)"
            await body.mount(Label("MCP presets, comma-separated. " + names))
            await body.mount(Input(value=self.answers["mcp"], id="mcp", placeholder="filesystem, github"))
        else:
            await body.mount(Label("Your name"))
            await body.mount(Input(value=self.answers["owner"], id="owner"))
            await body.mount(Label("Assistant name"))
            await body.mount(Input(value=self.answers["assistant"], id="assistant"))
            await body.mount(Label("Tone"))
            await body.mount(Input(value=self.answers["tone"], id="tone"))
            await body.mount(Label("Timezone"))
            await body.mount(Input(value=self.answers["timezone"], id="timezone"))
            await body.mount(Label("Consolidation hour"))
            await body.mount(Input(value=self.answers["sleep"], id="sleep"))
        nxt = self.query_one("#next", Button)
        nxt.label = "Save" if self.step == len(STEPS) - 1 else "Next"

    def _capture(self) -> None:
        for field in (
            "provider",
            "model",
            "embeddings",
            "context",
            "memory",
            "persona",
            "mcp",
            "owner",
            "assistant",
            "tone",
            "timezone",
            "sleep",
            "new_kind",
            "new_name",
        ):
            found = self.query(f"#{field}")
            if found:
                self.answers[field] = str(found.first().value)
        key = self.query("#api_key")
        if key and str(key.first().value).strip():
            self.answers["api_key"] = str(key.first().value)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        self._capture()
        if event.button.id == "back":
            self.step = max(0, self.step - 1)
            await self._show()
            return
        if event.button.id != "next":
            return
        if self.answers["new_kind"] and self.answers["new_name"]:
            from iris_ai.cli.scaffold import write_new
            from iris_ai.plug import FOLDER_KINDS

            if self.answers["new_kind"] in {*FOLDER_KINDS, "role"}:
                write_new(self.answers["new_kind"], self.answers["new_name"], Path("examples"))
            self.answers["new_kind"] = ""
            self.answers["new_name"] = ""
        if self.step < len(STEPS) - 1:
            self.step += 1
            await self._show()
            return
        mcp = tuple(part.strip() for part in self.answers["mcp"].split(",") if part.strip())
        apply_wizard(
            self.root,
            provider=self.answers["provider"] or "auto",
            api_key=self.answers["api_key"],
            model=self.answers["model"],
            embeddings=self.answers["embeddings"] or "none",
            context=self.answers["context"] or "default",
            memory=self.answers["memory"] or "sqlite",
            persona=self.answers["persona"] or "file",
            mcp=mcp,
            owner_name=self.answers["owner"],
            assistant_name=self.answers["assistant"] or "assistant",
            tone=self.answers["tone"],
            timezone=self.answers["timezone"] or "UTC",
            sleep_hour=self.answers["sleep"] or "4",
        )
        self.exit(0)


def run_wizard(root: Path, *, start: str = "provider") -> int:
    WizardApp(root, start=start).run()
    return 0
