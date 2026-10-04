"""Full-screen chat. Plain output stays available for pipes and `--once`."""

from __future__ import annotations

import uuid
from pathlib import Path

from rich.console import Group
from rich.markdown import Markdown
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Footer, Input, Static

from iris_ai.cli.tui.approval import ApprovalScreen
from iris_ai.cli.tui.input import prompt_for
from iris_ai.cli.tui.palette import COMMANDS, matching
from iris_ai.cli.tui.statusbar import render_status
from iris_ai.cli.tui.tool_card import ToolCard
from iris_ai.cli.tui.transcript import note_line, user_line
from iris_ai.config import settings
from iris_ai.engine import harness
from iris_ai.kernel.events import (
    Done,
    ErrorEvent,
    NodeUpdate,
    TextDelta,
    ToolEnd,
    ToolStart,
    Usage,
    event_from_custom,
)
from iris_ai.prompt import assistant_name


class Line(Static):
    """A plain transcript line. `plain` is what tests read."""

    def __init__(self, text: str) -> None:
        self.plain = text
        super().__init__(text)


class MarkdownReply(Static):
    """An assistant reply, rendered as markdown."""

    def __init__(self, speaker: str, body: str) -> None:
        super().__init__()
        self.speaker = speaker
        self.body = body

    def render(self):
        return Group(Text(self.speaker, style="bold"), Markdown(self.body or ""))


def _coerce(item: object):
    """Accept a typed event or a legacy `(mode, payload)` pair."""
    if isinstance(item, tuple) and len(item) == 2:
        mode, payload = item
        if mode == "error":
            return ErrorEvent(text=str(payload))
        if mode == "custom" and isinstance(payload, dict):
            return event_from_custom(payload)
        if mode == "updates" and isinstance(payload, dict) and payload:
            node, update = next(iter(payload.items()))
            return NodeUpdate(str(node), update if isinstance(update, dict) else {})
        return None
    if hasattr(item, "kind"):
        return item
    return None


def _parse_parallel(text: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in text.split("|"):
        role, sep, task = part.partition(":")
        if sep and role.strip() and task.strip():
            pairs.append((role.strip(), task.strip()))
    return pairs


def _message_text(message: object) -> str:
    """Reply text from a node update, ignoring tool-call messages."""
    if isinstance(message, dict):
        mtype = message.get("type") or message.get("role") or ""
        calls = message.get("tool_calls")
        content = message.get("content")
    else:
        mtype = getattr(message, "type", "") or getattr(message, "role", "")
        calls = getattr(message, "tool_calls", None)
        content = getattr(message, "content", "")
    if mtype not in {"ai", "assistant"} or calls:
        return ""
    if isinstance(content, str):
        return content
    return str(content or "")


class ChatApp(App):
    CSS_PATH = Path(__file__).with_name("theme.tcss")
    BINDINGS = [
        Binding("ctrl+c", "quit", "quit"),
        Binding("ctrl+p", "palette", "commands"),
        Binding("tab", "complete", "complete", show=False),
    ]

    def __init__(self, session: str, *, brain=None) -> None:
        super().__init__()
        self.session = session
        self._preset = brain
        self._brain = None
        self._stack = None
        self._busy = False
        self._booted = False
        self._tokens = 0
        self._cost = 0.0
        self._open_tools: list[ToolCard] = []

    def compose(self) -> ComposeResult:
        yield Static("", id="status")
        yield VerticalScroll(id="transcript")
        with Vertical(id="bottom"):
            yield Static("", id="suggest")
            yield Input(placeholder="message, or /help", id="prompt")
            yield Footer()

    def on_mount(self) -> None:
        self._set_status("starting")
        self.query_one("#suggest", Static).display = False
        self.run_worker(self._boot(), exclusive=False, name="boot")

    async def _boot(self) -> None:
        try:
            if self._preset is not None:
                self._brain = self._preset
            else:
                self._stack = harness(services=False)
                self._brain = await self._stack.__aenter__()
            name = assistant_name(self._root())
            self.query_one("#prompt", Input).placeholder = prompt_for(name)
            self._booted = True
            self._set_status()
            await self._write(note_line("type /help for commands"))
            self.query_one("#prompt", Input).focus()
        except Exception as exc:  # noqa: BLE001 - the screen reports a boot failure
            await self._write(note_line(f"{type(exc).__name__}: {exc}"))
            self._set_status("failed to start")

    async def on_unmount(self) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(None, None, None)

    def _root(self) -> Path:
        if self._brain is not None:
            return self._brain.runtime.files.root
        return Path(settings.workspace_dir)

    async def _write(self, text: str) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Line(text))
        transcript.scroll_end(animate=False)

    async def _mount(self, widget) -> None:
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(widget)
        transcript.scroll_end(animate=False)

    def _set_status(self, note: str = "") -> None:
        self.query_one("#status", Static).update(
            render_status(
                name=assistant_name(self._root()),
                provider=getattr(settings, "_resolved_provider", None) or settings.llm_provider,
                model=settings.strong_model or settings.cheap_model or "auto",
                session=self.session,
                note=note,
                tokens=self._tokens,
                cost=self._cost,
            )
        )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "prompt":
            return
        box = self.query_one("#suggest", Static)
        hits = matching(event.value) if event.value.startswith("/") else []
        if hits and event.value.strip() != hits[0][0]:
            box.update("\n".join(f"{name}  {blurb}" for name, blurb in hits[:8]))
            box.display = True
        else:
            box.display = False

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        event.input.value = ""
        self.query_one("#suggest", Static).display = False
        if not text or self._busy:
            return
        if not self._booted or self._brain is None:
            await self._write(note_line("still starting"))
            return
        self._busy = True
        self.run_worker(self._handle(text), exclusive=True, name="turn")

    async def _handle(self, text: str) -> None:
        try:
            if text.startswith("/"):
                await self._slash(text)
            else:
                await self._write(user_line(text))
                await self._turn(text)
        finally:
            self._busy = False
            self._set_status()

    async def _turn(self, text: str) -> None:
        name = assistant_name(self._root())
        chunks: list[str] = []
        fallback = ""
        try:
            async for raw in self._brain.stream(text, session_id=self.session):
                event = _coerce(raw)
                if isinstance(event, TextDelta):
                    chunks.append(event.delta or "")
                elif isinstance(event, ToolStart):
                    call = event.call or {}
                    card = ToolCard(str(call.get("name") or "tool"))
                    self._open_tools.append(card)
                    await self._mount(card)
                elif isinstance(event, ToolEnd):
                    card = self._take_tool(event.name)
                    if card is None:
                        card = ToolCard(event.name or "tool")
                        await self._mount(card)
                    card.finish(ok=event.ok, summary=event.summary, duration_ms=event.duration_ms)
                elif isinstance(event, Usage):
                    self._tokens += int(event.tokens or 0)
                    self._cost += float(event.cost or 0.0)
                    self._set_status()
                elif isinstance(event, ErrorEvent):
                    await self._write(note_line(event.text or "error"))
                elif isinstance(event, Done) and event.text:
                    fallback = fallback or event.text
                elif isinstance(event, NodeUpdate):
                    for message in (event.update or {}).get("messages") or []:
                        found = _message_text(message)
                        if found:
                            fallback = found
            body = "".join(chunks).strip() or str(fallback).strip()
            if body:
                await self._mount(MarkdownReply(name, body))
            pending = await self._brain.graph.threads.pending_approval(self.session)
            if pending:
                decision = await self.push_screen_wait(ApprovalScreen(pending))
                reply = await self._brain.resume(self.session, decision=decision or "denied")
                if reply:
                    await self._mount(MarkdownReply(name, str(reply)))
        except Exception as exc:  # noqa: BLE001 - the screen reports it; the library still raises
            await self._write(note_line(f"{type(exc).__name__}: {exc}"))

    def _role_names(self) -> list[str]:
        roles = getattr(getattr(self._brain.runtime, "orchestrator", None), "roles", {}) or {}
        return list(roles)

    async def _fanout(self, pairs: list[tuple[str, str]], *, title: str) -> None:
        orchestrator = getattr(self._brain.runtime, "orchestrator", None)
        if orchestrator is None:
            await self._write(note_line("parallel agents are not available"))
            return
        cards: dict[str, ToolCard] = {}
        for role, _task in pairs:
            card = ToolCard(role)
            cards[role] = card
            await self._mount(card)
        try:
            handoffs = await orchestrator.parallel(pairs, session_id=self.session)
        except Exception as exc:  # noqa: BLE001 - the screen reports a failed fan-out
            await self._write(note_line(f"{type(exc).__name__}: {exc}"))
            return
        for handoff, (role, _task) in zip(handoffs, pairs, strict=False):
            refused = bool(getattr(handoff, "refused", ""))
            summary = str(getattr(handoff, "refused", "") or "done")
            spend = getattr(handoff, "spend", None)
            ms = float(getattr(spend, "ms", 0) or 0)
            card = cards.get(role)
            if card is not None:
                card.finish(ok=not refused, summary=summary[:80], duration_ms=ms)
        text, _truncated = orchestrator.merge(handoffs)
        await self._mount(MarkdownReply(title, text))

    def _take_tool(self, name: str) -> ToolCard | None:
        for index in range(len(self._open_tools) - 1, -1, -1):
            card = self._open_tools[index]
            if card.tool_name == name or not name:
                return self._open_tools.pop(index)
        if self._open_tools:
            return self._open_tools.pop()
        return None

    async def _slash(self, text: str) -> None:
        head, _, rest = text.partition(" ")
        head = head.lower()
        if head in {"/exit", "/quit"}:
            self.exit()
            return
        if head == "/help":
            for name, blurb in COMMANDS:
                await self._write(note_line(f"{name}  {blurb}"))
            return
        if head == "/clear":
            await self.query_one("#transcript", VerticalScroll).remove_children()
            return
        if head == "/new":
            self.session = uuid.uuid4().hex[:12]
            self._open_tools.clear()
            self._set_status("new session")
            await self._write(note_line(f"session {self.session}"))
            return
        if head == "/switch" and rest.strip():
            self.session = rest.strip()
            self._set_status()
            await self._write(note_line(f"session {self.session}"))
            return
        if head in {"/sessions", "/threads"}:
            names = await self._brain.graph.threads.list_threads()
            await self._write(note_line(", ".join(names) or "(no saved sessions yet)"))
            await self._write(note_line("switch with /switch <name>"))
            return
        if head == "/model":
            from iris_ai.cli.models import switch_model

            if rest.strip():
                await self._write(note_line(switch_model(rest.strip())))
            else:
                await self._write(note_line(settings.strong_model or settings.cheap_model or "auto"))
            self._set_status()
            return
        if head == "/provider":
            from iris_ai.cli.models import switch_provider

            if rest.strip():
                await self._write(note_line(switch_provider(rest.strip())))
            else:
                await self._write(note_line(settings.llm_provider))
            self._set_status()
            return
        if head == "/mcp":
            from iris_ai.mcp.catalog import load_catalog

            declared = []
            path = Path(settings.mcp_servers_file)
            if path.is_file():
                import json

                try:
                    declared = list(json.loads(path.read_text(encoding="utf-8")).get("mcpServers", {}))
                except (OSError, json.JSONDecodeError):
                    declared = []
            await self._write(note_line("presets: " + ", ".join(load_catalog())))
            await self._write(note_line("declared: " + (", ".join(declared) or "(none)")))
            await self._write(note_line("add one with `iris mcp add <preset>`"))
            return
        if head == "/roles":
            roles = getattr(getattr(self._brain.runtime, "orchestrator", None), "roles", {}) or {}
            await self._write(note_line(", ".join(roles) or "(no roles)"))
            return
        if head == "/reload":
            if self._brain is None:
                await self._write(note_line("still starting"))
                return
            message = await self._brain.reload()
            await self._write(note_line(message))
            if "built-in persona" in message:
                self._set_status("persona fell back to the built-in")
            else:
                self._set_status("reloaded")
            return
        if head == "/components":
            from iris_ai.components import OPTIONS, list_options

            for kind in OPTIONS:
                await self._write(note_line(f"{kind}: {', '.join(list_options(kind))}"))
            return
        if head == "/team":
            if not rest.strip():
                await self._write(note_line("usage: /team <question>"))
                return
            await self._fanout([(name, rest.strip()) for name in self._role_names()], title="team")
            return
        if head == "/parallel":
            pairs = _parse_parallel(rest)
            if not pairs:
                await self._write(note_line("usage: /parallel role: task | role: task"))
                return
            await self._fanout(pairs, title="parallel")
            return
        if head == "/persona":
            path = self._root() / "PERSONA.md"
            body = path.read_text(encoding="utf-8").strip() if path.is_file() else ""
            await self._write(note_line(body or "(no persona yet — iris config)"))
            return
        if head == "/config":
            from iris_ai.cli.tui.setup import SetupScreen

            saved = await self.push_screen_wait(SetupScreen(self._root()))
            await self._write(note_line("setup saved" if saved else "setup cancelled"))
            return
        if head == "/memory":
            await self._write(note_line(str(self._root())))
            return
        if head == "/search":
            await self._write(user_line(text))
            await self._turn(f"Search memory for: {rest.strip()}")
            return
        if head == "/dream":
            await self._write(note_line("consolidating memory"))
            dreams = getattr(self._brain.runtime, "dreams", None)
            if dreams is None or not hasattr(dreams, "sleep"):
                await self._write(note_line("consolidation is off"))
                return
            record = await dreams.sleep()
            await self._write(note_line(getattr(record, "summary", None) or "consolidation finished"))
            return
        if head == "/forget":
            if not rest.strip():
                await self._write(note_line("usage: /forget <what to retire>"))
                return
            await self._write(user_line(text))
            await self._turn(f"Forget this memory: {rest.strip()}")
            return
        if head == "/tools":
            from iris_ai.agent.tools import TOOL_NAMES

            await self._write(note_line(", ".join(TOOL_NAMES)))
            return
        if head == "/skills":
            from iris_ai.skills.registry import open_registry

            registry = open_registry(self._brain.runtime.files)
            names = [skill.name for skill in registry.list()]
            await self._write(note_line(", ".join(names) or "(no skills)"))
            return
        if head == "/costs":
            totals = self._brain.ledger.totals()
            tokens = int(totals.get("prompt_tokens", 0)) + int(totals.get("completion_tokens", 0))
            await self._write(
                note_line(
                    f"{totals.get('requests', 0)} calls, {tokens} tokens, ${float(totals.get('cost', 0)):.4f}"
                )
            )
            return
        if head in {"/trace", "/explain"}:
            from iris_ai.explain import explain_latest

            path = self._root() / "config" / "traces.jsonl"
            text = explain_latest(path, session=self.session, include_path=head == "/trace")
            for line in text.splitlines():
                await self._write(note_line(line or " "))
            return
        hits = matching(head)
        if hits:
            await self._write(note_line("  ".join(name for name, _ in hits)))
            return
        await self._write(note_line(f"unknown command {head}; /help lists them"))

    def action_palette(self) -> None:
        prompt = self.query_one("#prompt", Input)
        if not prompt.value.startswith("/"):
            prompt.value = "/"
            prompt.cursor_position = 1

    def action_complete(self) -> None:
        prompt = self.query_one("#prompt", Input)
        hits = matching(prompt.value)
        if not hits:
            return
        needs_arg = hits[0][0] in {"/switch", "/search", "/forget"}
        prompt.value = hits[0][0] + (" " if needs_arg else "")
        prompt.cursor_position = len(prompt.value)
        self.query_one("#suggest", Static).display = False


def run_tui(*, session: str = "cli") -> int:
    from iris_ai.cli import ui
    from iris_ai.cli.chat import _provider_configured
    from iris_ai.cli.help_theme import console

    if not _provider_configured():
        out = console(stderr=True)
        ui.failed(out, "error", "no LLM provider key found")
        ui.hint(out, "hint: `iris doctor` shows which key names are missing; put one in .env")
        return 1
    ChatApp(session=session).run()
    return 0
