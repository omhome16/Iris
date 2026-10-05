"""`iris init` — make a checkout ready in one command, then prove it.

The quickstart used to be four manual steps before the first reply: copy
`.env.example`, fill in a key, copy `config/harness.toml.example`, and start
Postgres with Docker Compose. Three of those are mechanical, and the fourth is no
longer needed at all now that the default memory backend is a single SQLite file
— so `iris init` does the mechanical part:

    .env                    created from .env.example
    config/harness.toml     created from the example (the file the engine reads)

Copying files is not the interesting half. The interesting half is that the setup
is **measured, not asserted** — three probes, one per thing a session needs:

- **a model** — one no-op completion on the cheap tier, so "your key works" is a
  result rather than a hope;
- **an index** — the configured store is opened and asked for its stats, which is
  also the check that the no-service claim holds (SQLite needs no daemon);
- **a thread store** — SQLite, Postgres, then memory, so the report says
  which tier a conversation would actually land in, and warns when that tier is
  the in-memory one (threads that do not survive exit).

Recall is reported separately, and honestly: the default backend runs
keyword-only when no embedding provider is configured. That is a working install,
not a broken one — but a keyword-only index that does not say so is exactly how
one gets mistaken for the other, so this command names the fix and the
alternatives instead of printing a green tick.

Deliberately **not a questionnaire**. Which provider has a key, where the
workspace is, and which store is configured can all be detected, and a prompt
would only make the five-minute path slower and the command untestable. `iris doctor` stays the read-only inspection;
`iris init` is the setup, and both use the same names and the same ok/warn/fail
vocabulary.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path

from iris_ai.capabilities.memory import MEMORY_BACKENDS
from iris_ai.capabilities.models import MODELS
from iris_ai.cli import ui
from iris_ai.cli.doctor import PROVIDER_KEY_NAMES, Check, _load_dotenv, _resolve_provider
from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.engine import _open_checkpointer
from iris_ai.manifest import apply_manifest, load_manifest
from iris_ai.providers import PROVIDERS

#: How long the no-op model check may take. A provider that cannot answer a
#: one-word prompt inside this is a setup problem to report now, not to wait on.
_CHECK_TIMEOUT = 30.0

#: Printed when recall is keyword-only. Two ways in, because "set a cloud key"
#: is not the only answer for a local-first assistant: a local embedding model
#: needs no key at all.
_TURN_ON_VECTORS = (
    "Semantic recall is off: recall is keyword search only until an embedding "
    "provider exists.\n"
    "  To turn it on: put GEMINI_API_KEY in .env (the default embedding model is "
    "gemini/gemini-embedding-001);\n"
    "  or run a local model instead with LLM_PROVIDER=ollama "
    "(ollama/nomic-embed-text needs no key).\n"
    "  Either way, start any command that boots the harness — every boot "
    "re-indexes the workspace,\n"
    "  so notes written before the key existed get embeddings then."
)


def _checkout_root() -> Path | None:
    """The directory holding the sample files, when this is a checkout.

    cwd first (the quickstart runs `iris init` at the checkout root), then the
    tree the package was imported from — the same directory for an editable
    install and for `uv run`.
    """
    for candidate in (Path.cwd(), Path(__file__).resolve().parents[3]):
        if (candidate / ".env.example").is_file():
            return candidate
    return None


def _sample_check(source: Path, dest: Path, *, force: bool) -> Check:
    """Copy one sample file, and say which of the three things happened.

    The destination is checked *first*: a file that is already there is "kept"
    whether or not a template exists for it. That ordering is what lets
    `HARNESS_CONFIG=examples/assistant/harness.toml iris init` use a manifest that
    ships with its own file instead of warning about a template it never needed.
    """
    if dest.exists() and not force:
        # Never overwrite: `.env` holds the owner's keys, and silently replacing
        # them would be the worst possible behaviour for a setup command.
        return Check(str(dest), "ok", "kept (already exists)")
    if not source.is_file():
        return Check(str(dest), "warn", f"no template at {source} — create it by hand")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, dest)
    return Check(str(dest), "ok", f"created from {source}")


#: The neutral profile the harness ships, and where it lands in a workspace.
#: Both are files the owner is meant to edit, so they are written only when the
#: workspace has no copy — `--force` covers `.env` and the manifest, not these.
_WORKSPACE_TEMPLATES: dict[str, str] = {
    "AGENTS.md": "AGENTS.md",
    "WORKSPACE-README.md": "README.md",
}


def _workspace_checks() -> list[Check]:
    """Give a fresh workspace the neutral contract, and never touch one that has
    its own.

    Without this an installed wheel starts with an empty workspace and therefore
    no operating contract at all — the agent would have memory and no standing
    instructions. With it, a clone and an installed copy begin in the same place,
    and editing `AGENTS.md` is the first thing the owner does rather than the
    thing they never notice is missing.
    """
    root = Path(settings.workspace_dir)
    templates = Path(__file__).resolve().parents[1] / "templates"
    return [
        _sample_check(templates / packaged, root / name, force=False)
        for packaged, name in _WORKSPACE_TEMPLATES.items()
    ]


def _write_samples(config_path: Path, *, force: bool) -> list[Check]:
    root = _checkout_root()
    env_file = Path(".env")
    if root is None:
        return [
            Check(str(env_file), "warn", "no .env.example found — not a source checkout?"),
            Check(str(config_path), "warn", "no harness.toml.example found"),
        ]
    return [
        _sample_check(root / ".env.example", env_file, force=force),
        _sample_check(
            root / config_path.parent / f"{config_path.name}.example",
            config_path,
            force=force,
        ),
    ]


def _provider_checks(env: dict[str, str]) -> list[Check]:
    """Which provider would answer, and from which key. Values are never shown."""
    present = [name for name in PROVIDER_KEY_NAMES if env.get(name, "").strip()]
    if present:
        keys = Check("provider keys", "ok", ", ".join(present))
    else:
        keys = Check(
            "provider keys",
            "warn",
            "none set — set one, or run a local model with LLM_PROVIDER=ollama",
        )
    requested = (env.get("LLM_PROVIDER", "") or "auto").strip().lower() or "auto"
    resolved = _resolve_provider(requested, env)
    checks = [keys, Check("provider", "ok", f"{PROVIDERS[resolved].label} (LLM_PROVIDER={requested})")]
    from iris_ai.cli.doctor import stale_model_checks

    checks.extend(stale_model_checks(env))
    return checks


async def _model_check(llm) -> Check:
    """One no-op completion on the cheap tier — the setup, measured."""
    started = time.monotonic()
    try:
        reply = await llm.complete(
            [
                {"role": "system", "content": "Reply with the single word: ready"},
                {"role": "user", "content": "ready?"},
            ],
            tier="cheap",
            max_tokens=8,
            timeout=_CHECK_TIMEOUT,
            # A setup check reports the first failure; retrying it only delays
            # the message the owner needs.
            max_attempts=1,
        )
    except Exception as exc:  # noqa: BLE001 — any provider failure *is* the result
        detail = f"{type(exc).__name__}: {str(exc)[:160]}"
        return Check("model check", "fail", detail)
    elapsed = time.monotonic() - started
    said = (reply or "").strip().replace("\n", " ")[:40]
    if not said:
        # Reasoning models often spend a tiny cap on hidden tokens and return
        # no visible text. The call itself succeeded.
        return Check(
            "model check",
            "warn",
            f"cheap tier answered in {elapsed:.1f}s with an empty reply "
            "(the call succeeded; reasoning models do this under a small token cap)",
        )
    return Check("model check", "ok", f"cheap tier answered in {elapsed:.1f}s ({said!r})")


async def _memory_checks(llm, *, offline: bool) -> list[Check]:
    """Open the configured store, then ask whether recall can use embeddings."""
    index = MEMORY_BACKENDS.build(
        settings.memory_backend, dsn=settings.postgres_dsn, llm=llm, reranker=None
    )
    try:
        await index.connect()
    except Exception as exc:  # noqa: BLE001 — an unreachable store is the check result
        detail = f"{settings.memory_backend} unreachable ({type(exc).__name__}: {str(exc)[:120]})"
        return [
            Check("memory store", "fail", detail),
            Check("recall", "fail", "no store, so no recall — see the memory store line"),
        ]
    try:
        stats = await index.stats()
    finally:
        await index.close()

    store = Check(
        "memory store",
        "ok",
        f"{stats.get('backend', settings.memory_backend)} — {stats.get('total_chunks', 0)} chunks "
        f"at {stats.get('location', settings.sqlite_path)}",
    )
    if offline:
        return [store, Check("recall", "warn", "embeddings not probed (--offline)")]
    if not (settings.embedding_model or "").strip():
        notice = getattr(settings, "embedding_notice", "")
        detail = f"keyword-only ({notice})" if notice else "keyword-only (embeddings off)"
        return [store, Check("recall", "warn", detail)]
    try:
        await llm.embed_one("iris embedding probe", max_attempts=1)
    except Exception as exc:  # noqa: BLE001 — a missing embedder is a warn, not a fail
        return [store, Check("recall", "warn", f"keyword-only ({type(exc).__name__}: {str(exc)[:90]})")]
    return [store, Check("recall", "ok", "hybrid — keyword search plus embeddings")]


async def _threads_check() -> Check:
    """Walk the thread stores: which tier would hold the thread?"""
    async with AsyncExitStack() as stack:
        _saver, tier = await _open_checkpointer(stack, postgres="auto")
    if tier == "postgres":
        return Check("threads", "ok", "postgres — shared, survives restarts")
    if tier == "sqlite":
        return Check("threads", "ok", f"sqlite at {settings.checkpointer_path} — survives restarts")
    return Check("threads", "warn", "in-memory — threads are lost when the process exits")


async def _verify(*, offline: bool) -> tuple[list[Check], bool]:
    """Probe everything a session needs; returns the checks and whether recall is hybrid.

    The manifest has already been applied by `run` — it is what a boot applies, so
    both the files written and the checks reported describe the configuration a
    turn would run under rather than the defaults.
    """
    env = {**_load_dotenv(Path(".env")), **os.environ}
    checks = _provider_checks(env)

    try:
        llm = MODELS.build(settings.model_backend, ledger=None)
    except Exception as exc:  # noqa: BLE001 — an unknown backend is a clear setup error
        detail = f"model backend {settings.model_backend!r} unavailable ({exc})"
        return [*checks, Check("model backend", "fail", detail)], False

    if offline:
        checks.append(Check("model check", "warn", "skipped (--offline)"))
    else:
        checks.append(await _model_check(llm))

    memory = await _memory_checks(llm, offline=offline)
    checks.extend(memory)
    checks.append(await _threads_check())
    recall_ok = any(c.name == "recall" and c.level == "ok" for c in memory)
    return checks, recall_ok


def _render(files: list[Check], checks: list[Check], *, recall_ok: bool) -> None:
    out = console()
    ui.header(out, "Iris setup", "write the config, then measure the result")
    for label, rows in (("files", files), ("checks", checks)):
        if not rows:
            continue
        ui.section(out, label)
        for check in rows:
            ui.status(out, check.level, check.name, check.detail)
    out.print()
    ui.counts(out, [check.level for check in checks])
    if not recall_ok:
        out.print()
        out.print(_TURN_ON_VECTORS)


def _next_steps() -> None:
    out = console()
    out.print()
    ui.steps(
        out,
        "next",
        [
            ("iris", "open the chat (no service, no daemon)"),
            ("iris doctor", "re-check the environment any time"),
        ],
    )
    ui.note(
        out,
        "Postgres stays optional: keep the SQLite defaults, or point POSTGRES_DSN at a "
        "server and set MEMORY_BACKEND=pgvector",
    )


def run(
    *,
    config: Path | None = None,
    force: bool = False,
    offline: bool = False,
    yes: bool = False,
    provider: str = "",
    model: str = "",
    api_key_env: str = "",
    memory_mode: str = "",
) -> int:
    """Create the sample config, probe the setup, and report what it can do."""
    config_path = Path(config) if config is not None else Path(settings.harness_config)
    # Copy a missing sample *before* the wizard writes into it. Otherwise
    # `apply_plan` creates a sparse harness.toml and the sample step reports
    # "kept (already exists)" for a file this run just invented, and the
    # example template is never copied.
    files = _write_samples(config_path, force=force)
    # The manifest is applied before workspace files: it can name the workspace,
    # the index files and the store. (It used to be applied inside `_verify`,
    # which meant `iris init` reported one configuration and set up another.)
    apply_manifest(settings, load_manifest(config_path))
    if provider or (yes and model):
        from iris_ai.setup.flow import SetupPlan, apply_plan

        apply_plan(
            SetupPlan(
                provider=provider or "auto",
                model=model,
                api_key=os.environ.get(api_key_env, "") if api_key_env else "",
                embeddings=memory_mode or "none",
            )
        )
    elif yes:
        from iris_ai.cli.setup import apply_setup

        apply_setup(Path(settings.workspace_dir))
    elif sys.stdout.isatty() and not os.environ.get("IRIS_PLAIN"):
        from iris_ai.cli.ask import prompter_for
        from iris_ai.setup.flow import apply_plan, collect

        plan = collect(prompter_for(), skip_verify=offline)
        if plan is not None:
            apply_plan(plan)
    files = files + _workspace_checks()
    _restrict_env(Path(".env"))
    checks, recall_ok = asyncio.run(_verify(offline=offline))
    _render(files, checks, recall_ok=recall_ok)
    _next_steps()
    from iris_ai.cli.doctor import exit_code

    return exit_code(checks)


def _restrict_env(path: Path) -> None:
    """`.env` is owner-only, including a copy of the sample that holds no key yet."""
    if path.is_file():
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)
