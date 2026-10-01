"""Full-screen chat. Plain output stays available for pipes and `--once`."""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input, RichLog, Static

from iris_ai.cli.tui.approval import ApprovalScreen
from iris_ai.cli.tui.input import prompt_for
from iris_ai.cli.tui.palette import COMMANDS, matching
from iris_ai.cli.tui.statusbar import render_status
from iris_ai.cli.tui.transcript import assistant_line, note_line, tool_line, user_line
from iris_ai.config import settings
from iris_ai.engine import harness
from iris_ai.prompt import assistant_name


class ChatApp(App):
    CSS_PATH = Path(__file__).with_name("theme.tcss")
    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+p", "palette", "commands"),
    ]

    def __init__(self, session: str) -> None:
        super().__init__()
        self.session = session
        self._brain = None
        self._stack = None
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield RichLog(id="transcript", wrap=True, markup=False)
        yield Input(placeholder="message, or /help", id="prompt")
        yield Footer()

    async def on_mount(self) -> None:
        self._stack = harness(services=False)
        self._brain = await self._stack.__aenter__()
        name = assistant_name(self._brain.runtime.files.root)
        self.query_one("#prompt", Input).placeholder = prompt_for(name)
        self._set_status()
        self._log(note_line("type /help for commands"))

    async def on_unmount(self) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(None, None, None)

    def _log(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(text)

    def _set_status(self, note: str = "") -> None:
        root = self._brain.runtime.files.root if self._brain is not None else Path(settings.workspace_dir)
        self.query_one("#status", Static).update(
            render_status(
                name=assistant_name(root),
                provider=settings.llm_provider,
                model=settings.strong_model or settings.cheap_model or "auto",
                session=self.session,
                note=note,
            )
        )

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        if not text or self._busy:
            return
        if text.startswith("/"):
            await self._slash(text)
            return
        self._log(user_line(text))
        await self._turn(text)

    async def _turn(self, text: str) -> None:
        self._busy = True
        name = assistant_name(self._brain.runtime.files.root)
        chunks: list[str] = []
        try:
            async for event in self._brain.stream(text, session_id=self.session):
                kind = getattr(event, "kind", "")
                if kind == "text":
                    chunks.append(getattr(event, "delta", "") or "")
                elif kind == "tool_call":
                    self._log(tool_line(getattr(event, "name", "tool"), None))
                elif kind == "error":
                    self._log(note_line(getattr(event, "message", "error")))
            if chunks:
                self._log(assistant_line(name, "".join(chunks).strip()))
            pending = await self._brain.graph.threads.pending_approval(self.session)
            if pending:
                decision = await self.push_screen_wait(ApprovalScreen(pending))
                reply = await self._brain.resume(self.session, decision or "denied")
                if reply:
                    self._log(assistant_line(name, str(reply)))
        except Exception as exc:  # noqa: BLE001 - the screen reports it; the library still raises
            self._log(note_line(f"{type(exc).__name__}: {exc}"))
        finally:
            self._busy = False
            self._set_status()

    async def _slash(self, text: str) -> None:
        head, _, rest = text.partition(" ")
        head = head.lower()
        if head in {"/exit", "/quit"}:
            self.exit()
            return
        if head == "/help":
            for name, blurb in COMMANDS:
                self._log(note_line(f"{name}  {blurb}"))
            return
        if head == "/clear":
            self.query_one("#transcript", RichLog).clear()
            return
        if head == "/new":
            self.session = "cli"
            self._set_status("new session")
            return
        if head == "/switch" and rest.strip():
            self.session = rest.strip()
            self._set_status()
            return
        if head == "/sessions":
            names = await self._brain.graph.threads.list_threads()
            self._log(note_line(", ".join(names) or "(no saved sessions yet)"))
            return
        if head == "/model":
            self._log(note_line(settings.strong_model or settings.cheap_model or "auto"))
            return
        if head == "/provider":
            self._log(note_line(settings.llm_provider))
            return
        if head == "/persona":
            path = self._brain.runtime.files.root / "PERSONA.md"
            body = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
            self._log(note_line(body or "(no persona yet — iris config)"))
            return
        if head == "/config":
            from iris_ai.cli.tui.setup import run_setup

            run_setup(self._brain.runtime.files.root)
            return
        if head == "/memory":
            self._log(note_line(str(self._brain.runtime.files.root)))
            return
        if head == "/search":
            self._log(user_line(text))
            await self._turn(f"Search memory for: {rest.strip()}")
            return
        if head == "/dream":
            self._log(note_line("consolidating memory"))
            dreams = getattr(self._brain.runtime, "dreams", None)
            if dreams is None or not hasattr(dreams, "sleep"):
                self._log(note_line("consolidation is off"))
                return
            record = await dreams.sleep()
            self._log(note_line(getattr(record, "summary", None) or "consolidation finished"))
            return
        if head == "/forget":
            self._log(note_line("ask in a message, for example: forget that I moved"))
            return
        if head == "/tools":
            from iris_ai.agent.tools import TOOL_NAMES

            self._log(note_line(", ".join(TOOL_NAMES)))
            return
        if head == "/skills":
            from iris_ai.skills.registry import open_registry

            registry = open_registry(self._brain.runtime.files)
            names = [skill.name for skill in registry.list()]
            self._log(note_line(", ".join(names) or "(no skills)"))
            return
        if head == "/costs":
            self._log(note_line("run `iris costs` for the ledger"))
            return
        if head == "/trace":
            self._log(note_line(str(self._brain.runtime.files.root / "config" / "traces.jsonl")))
            return
        hits = matching(head)
        if hits:
            self._log(note_line("  ".join(name for name, _ in hits)))
            return
        self._log(note_line(f"unknown command {head}; /help lists them"))

    def action_palette(self) -> None:
        prompt = self.query_one("#prompt", Input)
        if not prompt.value.startswith("/"):
            prompt.value = "/"
            prompt.cursor_position = 1


def run_tui(*, session: str = "cli") -> int:
    ChatApp(session=session).run()
    return 0
