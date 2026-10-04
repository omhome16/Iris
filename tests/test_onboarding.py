"""`iris init` and `iris migrate` — the Phase 2 onboarding pair, offline.

The claim these commands make is "clone, run one command, talk to her, no
service" — so the tests run in a throwaway checkout with a SQLite file, a dead
DSN, and a model double reached through the capability registry. Nothing here
touches the network, and nothing here starts a daemon, because "no service" is
the thing being asserted rather than an environment assumption.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli_text import flat
from iris_ai.capabilities.memory import MEMORY_BACKENDS
from iris_ai.capabilities.models import MODELS
from iris_ai.cli.main import app
from iris_ai.config import settings
from iris_ai.registry import Registration

runner = CliRunner()
DEAD_DSN = "postgresql+psycopg://iris:iris@127.0.0.1:1/iris_test"


class SetupLLM:
    """The model double: answers the setup completion, and can withhold embeddings.

    Withholding them is not an error case — it is the default install (no
    embedding provider), which is exactly what `iris init` has to report
    truthfully instead of printing a green tick.
    """

    def __init__(self, *, embeddings: bool = True, fail: bool = False) -> None:
        self.embeddings = embeddings
        self.fail = fail
        self.completions = 0

    async def complete(self, messages, **kwargs):
        self.completions += 1
        if self.fail:
            raise RuntimeError("no provider key accepted")
        return "ready"

    async def embed(self, texts, **kwargs):
        return [await self.embed_one(text) for text in texts]

    async def embed_one(self, text):
        if not self.embeddings:
            raise RuntimeError("no embedding provider key")
        return [0.1] * 8


def _install_model(monkeypatch: pytest.MonkeyPatch, llm: SetupLLM) -> SetupLLM:
    entries = dict(MODELS._entries)
    entries["litellm"] = Registration(
        kind="model_backend",
        name="litellm",
        factory=lambda ledger=None, **kw: llm,
        source="test",
    )
    monkeypatch.setattr(MODELS, "_entries", entries)
    return llm


@pytest.fixture
def checkout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """A stand-in checkout: the sample files exist, cwd is inside, no services."""
    (tmp_path / ".env.example").write_text(
        "GEMINI_API_KEY=\nPOSTGRES_DSN=postgresql+psycopg://iris@localhost:5433/iris\n",
        encoding="utf-8",
    )
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "harness.toml.example").write_text(
        "[channels]\nenabled = []\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "harness_config", "config/harness.toml")
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path / "workspace"))
    monkeypatch.setattr(settings, "sandbox_dir", str(tmp_path / "workspace" / "sandbox"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "config" / "memory.db"))
    monkeypatch.setattr(settings, "checkpointer_backend", "auto")
    monkeypatch.setattr(settings, "checkpointer_path", str(tmp_path / "config" / "checkpoints.db"))
    monkeypatch.setattr(settings, "postgres_dsn", DEAD_DSN)
    monkeypatch.setattr(settings, "memory_backend", "sqlite")
    monkeypatch.setattr(settings, "typesafe_api_key", "")
    # The chunk-context pass is a model call per file; the onboarding commands
    # are not what proves it, and the double is a setup double, not a chat one.
    monkeypatch.setattr(settings, "contextual_chunking_enabled", False)
    _install_model(monkeypatch, SetupLLM())
    return tmp_path


def test_init_creates_the_sample_config_and_proves_the_setup(checkout: Path):
    """The five-minute path: one command, sample files written, setup measured."""
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (checkout / ".env").is_file()
    if os.name == "posix":
        assert stat.S_IMODE((checkout / ".env").stat().st_mode) == 0o600
    assert (checkout / "config" / "harness.toml").is_file()

    out = flat(result.stdout)
    assert "Iris setup" in out
    assert "model check" in out and "answered in" in out  # the probe actually ran
    assert "memory store" in out and "sqlite" in out
    assert "no daemon" in out  # the promise, stated where the user reads it


def test_init_reports_keyword_only_recall_and_how_to_change_it(checkout: Path, monkeypatch):
    """No embedding key is a *working* install — and it has to say what is missing."""
    _install_model(monkeypatch, SetupLLM(embeddings=False))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0, "a missing embedder is a warning, not a broken setup"
    out = result.stdout
    assert "keyword-only" in out
    # How to turn it on, and the keyless alternative — the onboarding decision
    # was that a user must not have to guess why recall is thinner.
    assert "GEMINI_API_KEY" in out
    assert "LLM_PROVIDER=ollama" in out
    assert "ollama/nomic-embed-text" in out


def test_init_never_overwrites_an_env_without_force(checkout: Path):
    """`.env` holds the owner's keys; replacing them would be the worst bug here."""
    env = checkout / ".env"
    env.write_text("GEMINI_API_KEY=owner-secret\n", encoding="utf-8")
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert env.read_text(encoding="utf-8") == "GEMINI_API_KEY=owner-secret\n"

    assert runner.invoke(app, ["init", "--force"]).exit_code == 0
    assert "owner-secret" not in env.read_text(encoding="utf-8")


def test_init_seeds_a_neutral_workspace_the_first_time(checkout: Path):
    """A fresh workspace gets the shipped neutral contract — otherwise an installed
    copy has memory and no standing instructions, which is the failure the wheel
    would otherwise ship with."""
    workspace = checkout / "workspace"
    assert not workspace.exists()
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert (workspace / "AGENTS.md").is_file()
    assert (workspace / "README.md").is_file()
    assert "Never edited by the agent" in (workspace / "AGENTS.md").read_text(encoding="utf-8")
    # Rich wraps a long temp path anywhere, including inside "AGENTS.md".
    # Dropping every whitespace character makes the label independent of width
    # and still rejects a real "kept" line.
    flat = re.sub(r"\s+", "", re.sub(r"\x1b\[[0-9;]*m", "", result.stdout))
    assert "AGENTS.md:createdfrom" in flat
    assert "AGENTS.md:kept" not in flat


def test_init_never_overwrites_the_workspace_the_owner_edited(checkout: Path):
    """`AGENTS.md` is the owner's instruction to the agent, so not even `--force`
    touches it: `--force` is documented as covering `.env` and the manifest."""
    workspace = checkout / "workspace"
    workspace.mkdir(parents=True)
    mine = "# AGENTS.md\n\n- Always answer in haiku.\n"
    (workspace / "AGENTS.md").write_text(mine, encoding="utf-8")
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["init", "--force"]).exit_code == 0
    assert (workspace / "AGENTS.md").read_text(encoding="utf-8") == mine
    assert "kept (already exists)" in flat(runner.invoke(app, ["init"]).stdout)


def test_init_fails_when_the_provider_cannot_answer(checkout: Path, monkeypatch):
    """The no-op completion is the point: a setup that cannot answer is a failure."""
    _install_model(monkeypatch, SetupLLM(fail=True))
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 1
    assert "no provider key accepted" in flat(result.stdout)


def test_init_offline_writes_config_without_calling_anything(checkout: Path, monkeypatch):
    """`--offline` must not spend a token: CI and pre-commit use it."""
    llm = _install_model(monkeypatch, SetupLLM())
    result = runner.invoke(app, ["init", "--offline"])
    assert result.exit_code == 0
    assert llm.completions == 0
    assert "skipped (--offline)" in flat(result.stdout)
    assert (checkout / "config" / "harness.toml").is_file()


async def _sqlite_chunks() -> list[dict]:
    index = MEMORY_BACKENDS.build("sqlite", dsn=settings.postgres_dsn, llm=SetupLLM(), reranker=None)
    await index.connect()
    try:
        return await index.list_chunks()
    finally:
        await index.close()


def test_migrate_rebuilds_the_index_from_the_markdown(checkout: Path):
    """The migration is a rebuild: the Markdown is re-read, not rows copied."""
    workspace = checkout / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "MEMORY.md").write_text(
        "# Memory\n\n- [7] Omar prefers jasmine tea (by owner)\n", encoding="utf-8"
    )
    env = checkout / ".env"
    env.write_text("GEMINI_API_KEY=owner-secret\nMEMORY_BACKEND=pgvector\n", encoding="utf-8")

    result = runner.invoke(app, ["migrate"])
    assert result.exit_code == 0
    assert "re-indexed into sqlite" in flat(result.stdout)

    text = env.read_text(encoding="utf-8")
    assert "MEMORY_BACKEND=sqlite" in text
    assert "GEMINI_API_KEY=owner-secret" in text, "unrelated .env lines must survive"

    # Not an async test on purpose: `iris migrate` owns its own event loop, and
    # a command that cannot be driven from one is the bug this would hide.
    chunks = asyncio.run(_sqlite_chunks())
    assert chunks, "the rebuild must leave the fact in the new store"
    assert any("jasmine" in chunk["content"] for chunk in chunks)


def test_migrate_dry_run_touches_nothing(checkout: Path):
    env = checkout / ".env"
    env.write_text("MEMORY_BACKEND=pgvector\n", encoding="utf-8")
    result = runner.invoke(app, ["migrate", "--dry-run"])
    assert result.exit_code == 0
    assert "would change" in flat(result.stdout)
    assert env.read_text(encoding="utf-8") == "MEMORY_BACKEND=pgvector\n"
    assert not Path(settings.sqlite_path).exists(), "a dry run must not create the store"


def test_migrate_refuses_the_degraded_stand_in(checkout: Path):
    """`null` stores nothing: migrating to it would lose recall while reporting ok."""
    result = runner.invoke(app, ["migrate", "--to", "null"])
    assert result.exit_code == 1
    assert "degraded stand-in" in flat(result.stdout)


def test_migrate_names_the_known_backends_on_a_typo(checkout: Path):
    result = runner.invoke(app, ["migrate", "--to", "sqlit"])
    assert result.exit_code == 1
    assert "unknown memory backend" in flat(result.stdout)
    assert "sqlite" in result.stdout
