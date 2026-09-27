"""`iris chat` — the REPL, driven against a fake harness.

No database, no network, no provider key: the harness is replaced with a
scripted stub, which is the point — the CLI's job is rendering and lifecycle,
and the pipeline underneath is covered by `test_harness.py`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from typer.testing import CliRunner

from iris_ai.cli import chat as chat_mod
from iris_ai.cli.main import app

runner = CliRunner()


class FakeBrain:
    def __init__(self, *, degraded: bool = False, approval: dict | None = None, reply: str = "hello from iris"):
        self.mode = "degraded" if degraded else "full"
        self.degraded_reason = "no Postgres at postgresql+psycopg://127.0.0.1:1/iris_test" if degraded else None
        self.reply = reply
        self.approval = approval
        self.sessions: list[str] = []
        self.decisions: list[str] = []

    async def stream(self, text: str, *, session_id: str = "default", image: str | None = None):
        self.sessions.append(session_id)
        yield "custom", {"kind": "tool_call", "call": {"name": "memory_search", "args": {"query": text}}}
        yield "custom", {"kind": "text", "delta": self.reply}
        if self.approval is not None:
            yield "custom", {"kind": "approval", "payload": self.approval}

    async def resume(self, session_id: str, *, decision: str):
        self.decisions.append(decision)
        return f"resumed:{decision}"


@pytest.fixture
def install(monkeypatch: pytest.MonkeyPatch):
    """Install a fake harness; returns (brain, harness_kwargs)."""
    state: dict = {"kwargs": {}}

    @asynccontextmanager
    async def _fake_harness(**kwargs):
        state["kwargs"] = kwargs
        yield state["brain"]

    def _install(**brain_kwargs) -> FakeBrain:
        state["brain"] = FakeBrain(**brain_kwargs)
        monkeypatch.setattr(chat_mod, "harness", _fake_harness)
        monkeypatch.setattr(chat_mod, "_provider_configured", lambda: True)
        return state["brain"]

    _install.state = state  # type: ignore[attr-defined]
    return _install


def test_once_runs_a_single_turn_and_exits(install):
    brain = install()
    result = runner.invoke(app, ["chat", "--once", "hi"])
    assert result.exit_code == 0
    assert "hello from iris" in result.stdout
    assert brain.sessions == ["cli"]
    # The CLI is a client, not a service: it must not start the scheduler or
    # claim the Telegram channel.
    assert install.state["kwargs"].get("services") is False


def test_session_flag_selects_the_thread(install):
    brain = install()
    result = runner.invoke(app, ["chat", "--once", "hi", "--session", "work"])
    assert result.exit_code == 0
    assert brain.sessions == ["work"]


def test_degraded_mode_is_announced_before_the_reply(install):
    """The banner goes to stderr on purpose, so `--once > out.txt` stays clean."""
    install(degraded=True)
    result = runner.invoke(app, ["chat", "--once", "hi"])
    assert result.exit_code == 0
    assert "degraded" in result.stderr
    assert "no Postgres" in result.stderr
    assert "hello from iris" in result.stdout
    assert "degraded" not in result.stdout


def test_repl_runs_a_turn_then_exits_on_command(install):
    brain = install()
    result = runner.invoke(app, ["chat"], input="hello\n/exit\n")
    assert result.exit_code == 0
    assert "hello from iris" in result.stdout
    assert brain.sessions == ["cli"]


def test_repl_exits_cleanly_on_eof(install):
    install()
    result = runner.invoke(app, ["chat"], input="hello\n")
    assert result.exit_code == 0


def test_repl_help_does_not_become_a_turn(install):
    brain = install()
    result = runner.invoke(app, ["chat"], input="/help\n/exit\n")
    assert result.exit_code == 0
    assert "/exit" in result.stdout
    assert brain.sessions == []


def test_approval_prompt_approves(install):
    brain = install(approval={"action": "forget", "target": "green tea"})
    result = runner.invoke(app, ["chat", "--once", "forget that"], input="y\n")
    assert result.exit_code == 0
    assert brain.decisions == ["approved"]
    assert "resumed:approved" in result.stdout


def test_approval_prompt_cancels_on_anything_else(install):
    brain = install(approval={"action": "forget", "target": "green tea"})
    result = runner.invoke(app, ["chat", "--once", "forget that"], input="n\n")
    assert result.exit_code == 0
    assert brain.decisions == ["cancelled"]
    assert "resumed:cancelled" in result.stdout


def test_missing_provider_key_is_actionable(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(chat_mod, "_provider_configured", lambda: False)
    result = runner.invoke(app, ["chat", "--once", "hi"])
    assert result.exit_code == 1
    assert "no LLM provider key" in result.stdout
    assert "iris doctor" in result.stdout


def test_provider_detection_accepts_ollama(monkeypatch: pytest.MonkeyPatch):
    """`LLM_PROVIDER=ollama` needs no key — doctor's rule, kept here."""
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert chat_mod._provider_configured() is True


def test_chat_help_documents_the_flags():
    result = runner.invoke(app, ["chat", "--help"])
    assert result.exit_code == 0
    assert "--session" in result.stdout
    assert "--once" in result.stdout
