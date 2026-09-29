"""`iris migrate` — move the memory index to another store.

The migration is a **rebuild, not a copy**, and that is a design decision rather
than a shortcut. The index is a derivative: the Markdown in `workspace/` is the
source of truth (that is why the degraded backend can accept writes as no-ops and
still lose nothing), so the only version of the data both stores already agree on
is the Markdown itself. Re-reading it into the new store therefore:

- cannot drift: there is no second serialization path to get subtly wrong, and no
  need for the two backends to agree on embedding dimensions, chunk ids or origin
  spellings;
- works when the old store is **already gone** — which is the situation this
  exists for, since the reason to migrate is that you stopped running Postgres;
- is tested by the same code path a normal boot uses, so a migration exercises
  `Reindexer` rather than a migration-only reader.

What it does:

1. writes `MEMORY_BACKEND=<target>` into `.env` (creating the line if it is
   absent, leaving every other line alone),
2. builds that backend and re-indexes the whole workspace into it,
3. reports what landed, and whether the new index can use embeddings.

`--dry-run` answers the same questions without touching `.env` or the index:
the file change it would make, and the chunks it would write.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from iris_ai.capabilities.memory import MEMORY_BACKENDS
from iris_ai.capabilities.models import MODELS
from iris_ai.cli.doctor import Check
from iris_ai.cli.help_theme import LEVEL_STYLE, console
from iris_ai.config import settings
from iris_ai.ledger import CostLedger
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.indexer import Reindexer

#: `null` is the degraded stand-in: it answers nothing and stores nothing, so
#: "migrate to null" would be a way to lose recall while looking successful.
_NOT_A_TARGET = "null"


@dataclass(frozen=True)
class _EnvEdit:
    path: Path
    outcome: str  # written | unchanged | created | missing
    detail: str


def _edit_env(path: Path, key: str, value: str, *, dry_run: bool) -> _EnvEdit:
    """Set one key in `.env`, preserving every other line.

    `.env` is the owner's file: comments, ordering and unrelated keys survive, and
    nothing is written at all when the line already says what it should.
    """
    line = f"{key}={value}"
    if not path.exists():
        if dry_run:
            return _EnvEdit(path, "created", f"would create it with {line}")
        path.write_text(f"{line}\n", encoding="utf-8")
        return _EnvEdit(path, "created", f"created with {line}")

    lines = path.read_text(encoding="utf-8").splitlines()
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        if stripped.split("=", 1)[0].strip() == key:
            if stripped == line:
                return _EnvEdit(path, "unchanged", f"{line} is already set")
            if dry_run:
                return _EnvEdit(path, "written", f"would change {stripped} to {line}")
            lines[index] = line
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return _EnvEdit(path, "written", f"changed {stripped} to {line}")

    if dry_run:
        return _EnvEdit(path, "written", f"would append {line}")
    lines.append(line)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return _EnvEdit(path, "written", f"appended {line}")


async def _reindex(target: str, *, dry_run: bool) -> tuple[list[Check], bool]:
    """Rebuild the workspace index in `target`; returns checks and whether vectors landed."""
    files = WorkspaceFiles(Path(settings.workspace_dir))
    ledger = CostLedger(files.root / "config" / "llm_calls.jsonl")
    llm = MODELS.build(settings.model_backend, ledger=ledger)

    if dry_run:
        # Count what the real run would index, using the same iterator the
        # reindexer uses — a dry run that guesses is worse than no dry run. The
        # placeholder store is `null` on purpose: a dry run must not open (or
        # create) the target, and `iter_indexable` reads the Markdown only.
        nothing = MEMORY_BACKENDS.build("null", dsn=settings.postgres_dsn, llm=llm, reranker=None)
        planned = len(Reindexer(files, nothing, llm).iter_indexable())
        return [
            Check(
                "memory",
                "ok",
                f"{planned} file(s) would be re-read into {target} "
                f"from {files.root} — the index is derived, so nothing is copied",
            )
        ], False

    index = MEMORY_BACKENDS.build(target, dsn=settings.postgres_dsn, llm=llm, reranker=None)
    await index.connect()
    try:
        chunks = await Reindexer(files, index, llm).reindex_all()
        stats = await index.stats()
    finally:
        await index.close()

    if stats.get("degraded"):
        return [
            Check("memory", "fail", f"{target} is degraded and stored nothing: {stats.get('reason', '')}")
        ], False

    embedded = int(stats.get("embedded_chunks", 0))
    vectors = bool(stats.get("vectors"))
    checks = [
        Check(
            "memory",
            "ok",
            f"{chunks} chunk(s) re-indexed into {target} at "
            f"{stats.get('location', settings.sqlite_path)} from {files.root}",
        )
    ]
    if vectors:
        checks.append(Check("recall", "ok", f"hybrid — {embedded} chunk(s) carry embeddings"))
    else:
        checks.append(
            Check(
                "recall",
                "warn",
                "keyword-only — no embeddings stored (no embedding provider key, "
                "or nothing indexed yet); recall still works, and `iris init` "
                "reports the exact fix",
            )
        )
    return checks, vectors


def _render(edit: _EnvEdit, checks: list[Check], *, dry_run: bool) -> None:
    out = console()
    out.print("[iris.title]Iris migrate[/iris.title]")
    level = "warn" if edit.outcome == "missing" else "ok"
    out.print(f"  [{LEVEL_STYLE[level]}]{edit.outcome:9}[/{LEVEL_STYLE[level]}] {edit.path}: {edit.detail}")
    for check in checks:
        style = LEVEL_STYLE[check.level]
        out.print(f"  [{style}]{check.level:9}[/{style}] {check.name}: {check.detail}")
    out.print()
    if dry_run:
        out.print("  nothing was written — drop --dry-run to migrate")
    else:
        out.print("  next: iris chat      (the Markdown, not the old store, is the source of truth)")
        out.print("  Postgres is no longer required; stop it whenever you like.")


def run(*, to: str = "sqlite", dry_run: bool = False, env_path: Path | None = None) -> int:
    """Rebuild the memory index in `to`, and point `.env` at it."""
    names = {reg.name for reg in MEMORY_BACKENDS.enabled()} or {"sqlite", "pgvector", "null"}
    if to == _NOT_A_TARGET:
        console().print(
            "[iris.fail]error[/iris.fail] `null` stores nothing — it is the degraded "
            "stand-in, not a migration target"
        )
        return 1
    if to not in names:
        console().print(
            f"[iris.fail]error[/iris.fail] unknown memory backend {to!r}; known: {sorted(names)}"
        )
        return 1

    edit = _edit_env(Path(".env") if env_path is None else env_path, "MEMORY_BACKEND", to, dry_run=dry_run)
    checks, _vectors = asyncio.run(_reindex(to, dry_run=dry_run))
    _render(edit, checks, dry_run=dry_run)
    return 1 if any(c.level == "fail" for c in checks) else 0
