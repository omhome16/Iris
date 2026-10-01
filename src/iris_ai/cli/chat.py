"""`iris chat` — a streaming REPL on the library's turn pipeline.

The CLI is a *client*, not a second brain: it opens the same `Harness` the HTTP
API opens and streams the same events `/chat/stream` sends. Two consequences
worth stating:

- a degraded session (no Postgres) is announced before the first prompt, and
  its recall is unavailable rather than silently empty;
- the default session id is `cli`, so a REPL thread never shares state with the
  API's `default` thread by accident.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from iris_ai.agent.chat import ApprovalRequired
from iris_ai.cli import art, ui
from iris_ai.cli.doctor import PROVIDER_KEY_NAMES, _load_dotenv
from iris_ai.cli.help_theme import console
from iris_ai.engine import Harness, harness

# Windows: the selector loop is the one async database connects can finish on.
# Same guard as iris_ai.api and scripts/run_core.py.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

REPL_HELP = """\
[iris.brand]commands[/iris.brand]
  [iris.cmd]/exit[/iris.cmd], [iris.cmd]/quit[/iris.cmd]  leave the chat
  [iris.cmd]/help[/iris.cmd]         this list

Anything else is a turn: it goes to the same pipeline the API and the Telegram
bridge use, so memory, judgments and traces behave identically.
"""

#: The prompt marks. `›` is cp1252-safe (0x9B) and rich degrades its own frames
#: for a console that cannot draw them; the *diagnostics* keep ASCII marks, because
#: they get pasted into issues where a mojibake byte looks like a bug.
YOU = "you › "
IRIS = "iris › "


def _provider_configured() -> bool:
    """A provider key in the environment, in `.env`, or a local Ollama.

    Mirrors `iris doctor`: names only, and `.env` merged under the process env
    so the documented quickstart (`cp .env.example .env`, fill it in) works.
    """
    if os.environ.get("LLM_PROVIDER", "").strip().lower() == "ollama":
        return True
    env = {**_load_dotenv(Path(".env")), **os.environ}
    return any(env.get(name, "").strip() for name in PROVIDER_KEY_NAMES)


def _print_degraded(brain: Harness) -> None:
    """On stderr on purpose: `iris chat --once > out.txt` must stay clean."""
    out = console(stderr=True)
    out.print(f"[iris.warn]degraded[/iris.warn] {brain.degraded_reason}")
    out.print(
        "[iris.warn]recall is unavailable this session[/iris.warn] "
        "— new memories are still written to the workspace and indexed later."
    )


async def _render_turn(brain: Harness, text: str, session: str) -> None:
    """One streamed turn, rendered as it happens."""
    out = console()
    out.print("[iris.brand]iris[/iris.brand] [iris.mark]›[/iris.mark] ", end="")
    printed = False
    thinking = False
    pending: dict | None = None

    async for kind, payload in brain.stream(text, session_id=session):
        if kind == "custom" and isinstance(payload, dict):
            event_kind = payload.get("kind")
            if event_kind == "thinking" and not thinking:
                thinking = True
                out.print("[iris.sub]thinking…[/iris.sub]", end="\r")
            elif event_kind == "text":
                delta = str(payload.get("delta", ""))
                if delta:
                    if thinking and not printed:
                        out.print(" " * 12, end="\r")  # clear the thinking line
                    out.print(delta, end="")
                    printed = True
            elif event_kind == "tool_call":
                call = payload.get("call") or {}
                name = call.get("name", "?")
                args = call.get("args", {})
                if printed:
                    out.print("")
                    printed = False
                else:
                    out.print("", end="\r")
                out.print(
                    f"[iris.sub]  {ui.CHEVRON} {name}({json.dumps(args, ensure_ascii=False)})[/iris.sub]"
                )
            elif event_kind == "approval":
                pending = payload.get("payload") or {}
        elif kind == "updates" and isinstance(payload, dict):
            for _node, update in payload.items():
                for message in (update or {}).get("messages", []):
                    mtype = message.get("type") if isinstance(message, dict) else getattr(message, "type", "")
                    calls = message.get("tool_calls") if isinstance(message, dict) else getattr(message, "tool_calls", None)
                    if mtype != "ai" or calls:
                        continue
                    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
                    if content and not printed:
                        out.print(str(content), end="")
                        printed = True

    if not printed:
        out.print("(no reply)", end="")
    out.print("")

    if pending is not None:
        await _handle_approval(brain, session, pending)


async def _handle_approval(brain: Harness, session: str, payload: dict) -> None:
    """The human-in-the-loop gate, in the terminal."""
    out = console()
    ui.header(out, "Approval needed", "an action outside the automatic policy is waiting")
    for key, value in payload.items():
        ui.grid(out, [(str(key), str(value))])
    out.print()
    try:
        answer = input("approve? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    decision = "approved" if answer in {"y", "yes"} else "cancelled"
    try:
        reply = await brain.resume(session, decision=decision)
    except ApprovalRequired:
        ui.warn(out, "still waiting on approval — say it again if you want to retry.")
        return
    except Exception as exc:  # noqa: BLE001 — a failed resume must not kill the REPL
        ui.failed(out, "error", f"resume failed: {exc}")
        return
    out.print(f"[iris.brand]iris[/iris.brand] [iris.mark]›[/iris.mark] {reply}")


async def _run_chat(*, session: str, once: str | None, debug: bool, no_banner: bool = False) -> int:
    out = console()

    if not _provider_configured():
        ui.failed(out, "error", "no LLM provider key found")
        ui.hint(out, "hint: `iris doctor` shows which key names are missing; put one in .env")
        ui.hint(out, "      (`cp .env.example .env` then set GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY)")
        return 1

    async with harness(services=False) as brain:
        if brain.mode == "degraded":
            _print_degraded(brain)

        if once is not None:
            try:
                await _render_turn(brain, once, session)
            except Exception as exc:  # the CLI formats errors, the library raises them
                if debug:
                    raise
                ui.failed(out, "error", f"{type(exc).__name__}: {exc}")
                ui.hint(out, "hint: re-run with --debug for a traceback")
                return 1
            return 0

        if art.banner_enabled(out, no_banner=no_banner):
            art.render_banner(out)
        ui.header(
            out,
            f"Iris chat - session '{session}'",
            "streaming; the same pipeline the API and the Telegram bridge use",
        )
        ui.note(out, "Ctrl+C or /exit to leave  ·  /help for the commands")
        while True:
            try:
                line = input(YOU)
            except (EOFError, KeyboardInterrupt):
                out.print("")
                return 0
            text = line.strip()
            if not text:
                continue
            if text in {"/exit", "/quit"}:
                return 0
            if text == "/help":
                out.print(REPL_HELP)
                continue
            if text == "/dream":
                dreams = getattr(brain.runtime, "dreams", None)
                if dreams is None or not hasattr(dreams, "sleep"):
                    ui.note(out, "consolidation is off")
                    continue
                record = await dreams.sleep()
                ui.note(out, getattr(record, "summary", None) or "consolidation finished")
                continue
            try:
                await _render_turn(brain, text, session)
            except Exception as exc:  # same contract as --once
                if debug:
                    raise
                ui.failed(out, "error", f"{type(exc).__name__}: {exc}")
                ui.hint(out, "hint: re-run with --debug for a traceback")


def _want_tui(*, once: str | None) -> bool:
    """The full-screen chat is for a real terminal. Pipes and `--once` stay plain."""
    if once is not None or os.environ.get("IRIS_PLAIN"):
        return False
    return sys.stdout.isatty()


def run_chat(
    *, session: str = "cli", once: str | None = None, debug: bool = False, no_banner: bool = False
) -> int:
    """Entry point used by the typer command (and by the tests)."""
    if _want_tui(once=once):
        try:
            from iris_ai.cli.tui.app import run_tui
        except ImportError:
            run_tui = None  # type: ignore[assignment]
        if run_tui is not None:
            return run_tui(session=session)
    try:
        return asyncio.run(_run_chat(session=session, once=once, debug=debug, no_banner=no_banner))
    except KeyboardInterrupt:
        # A deliberate exit from an interactive client is not a failure.
        return 0
    except Exception as exc:  # last-resort formatting for a boot crash
        if debug:
            raise
        err = console(stderr=True)
        ui.failed(err, "error", f"{type(exc).__name__}: {exc}")
        ui.hint(err, "hint: re-run with --debug for a traceback")
        return 1


if __name__ == "__main__":  # pragma: no cover - manual entry
    sys.exit(run_chat())
