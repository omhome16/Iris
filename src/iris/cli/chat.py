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

from iris.agent.chat import ApprovalRequired
from iris.cli import art
from iris.cli.doctor import PROVIDER_KEY_NAMES, _load_dotenv
from iris.cli.help_theme import console
from iris.engine import Harness, harness

REPL_HELP = """\
/exit, /quit   leave the chat
/help          this list

Anything else is a turn: it goes to the same pipeline the API and the Telegram
bridge use, so memory, judgments and traces behave identically.
"""


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
    out = console(stderr=True)
    out.print(f"[iris.warn]degraded[/iris.warn] {brain.degraded_reason}")
    out.print(
        "[iris.warn]         recall is unavailable this session[/iris.warn] "
        "— new memories are still written to the workspace and indexed later."
    )


async def _render_turn(brain: Harness, text: str, session: str) -> None:
    """One streamed turn, rendered as it happens."""
    out = console()
    out.print("[iris.title]iris>[/iris.title] ", end="")
    printed = False
    thinking = False
    pending: dict | None = None

    async for kind, payload in brain.stream(text, session_id=session):
        if kind == "custom" and isinstance(payload, dict):
            event_kind = payload.get("kind")
            if event_kind == "thinking" and not thinking:
                thinking = True
                out.print("[iris.warn]· thinking…[/iris.warn]", end="\r")
            elif event_kind == "text":
                delta = str(payload.get("delta", ""))
                if delta:
                    if thinking and not printed:
                        out.print(" " * 40, end="\r")  # clear the thinking line
                    out.print(delta, end="")
                    printed = True
            elif event_kind == "tool_call":
                call = payload.get("call") or {}
                name = call.get("name", "?")
                args = call.get("args", {})
                if not printed:
                    out.print("", end="\r")
                out.print(f"  [dim]· {name}({json.dumps(args, ensure_ascii=False)})[/dim]")
            elif event_kind == "approval":
                pending = payload.get("payload") or {}
        elif kind == "updates" and isinstance(payload, dict):
            for _node, update in payload.items():
                for message in (update or {}).get("messages", []):
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
    out.print("[iris.warn]I need your approval before doing that:[/iris.warn]")
    out.print(f"  {json.dumps(payload, ensure_ascii=False)}")
    try:
        answer = input("approve? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        answer = ""
    decision = "approved" if answer in {"y", "yes"} else "cancelled"
    try:
        reply = await brain.resume(session, decision=decision)
    except ApprovalRequired:
        out.print("[iris.warn]still waiting on approval — say it again if you want to retry.[/iris.warn]")
        return
    except Exception as exc:  # noqa: BLE001 — a failed resume must not kill the REPL
        out.print(f"[iris.fail]error[/iris.fail] resume failed: {exc}")
        return
    out.print(f"[iris.title]iris>[/iris.title] {reply}")


async def _run_chat(*, session: str, once: str | None, debug: bool, no_banner: bool = False) -> int:
    out = console()

    if not _provider_configured():
        out.print("[iris.fail]error[/iris.fail] no LLM provider key found")
        out.print("hint: `iris doctor` shows which key names are missing; put one in .env")
        out.print("      (`cp .env.example .env` then set GEMINI_API_KEY / GROQ_API_KEY / OPENROUTER_API_KEY)")
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
                out.print(f"[iris.fail]error[/iris.fail] {type(exc).__name__}: {exc}")
                out.print("hint: re-run with --debug for a traceback")
                return 1
            return 0

        if art.banner_enabled(out, no_banner=no_banner):
            art.render_banner(out)
        out.print(f"[iris.title]Iris chat[/iris.title] — session '{session}' (Ctrl+C or /exit to leave)")
        while True:
            try:
                line = input("you> ")
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
            try:
                await _render_turn(brain, text, session)
            except Exception as exc:  # same contract as --once
                if debug:
                    raise
                out.print(f"[iris.fail]error[/iris.fail] {type(exc).__name__}: {exc}")
                out.print("hint: re-run with --debug for a traceback")


def run_chat(
    *, session: str = "cli", once: str | None = None, debug: bool = False, no_banner: bool = False
) -> int:
    """Entry point used by the typer command (and by the tests)."""
    try:
        return asyncio.run(_run_chat(session=session, once=once, debug=debug, no_banner=no_banner))
    except KeyboardInterrupt:
        # A deliberate exit from an interactive client is not a failure.
        return 0
    except Exception as exc:  # last-resort formatting for a boot crash
        if debug:
            raise
        console(stderr=True).print(f"[iris.fail]error[/iris.fail] {type(exc).__name__}: {exc}")
        console(stderr=True).print("hint: re-run with --debug for a traceback")
        return 1


if __name__ == "__main__":  # pragma: no cover - manual entry
    sys.exit(run_chat())
