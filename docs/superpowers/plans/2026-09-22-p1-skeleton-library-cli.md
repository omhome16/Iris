# P1 — Library-First Skeleton + CLI Shell Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete the web dashboard, ship a real offline CLI (`iris --help|version|doctor`) on a library-first package, and leave tests/CI/docs honest — without changing agent, memory, JEV, or Telegram behavior.

**Architecture:** Keep `src/iris_ai` as the library (import path unchanged). Add a thin `src/iris_ai/cli/` package: one `typer.Typer` app in `main.py`, rich help in `help_theme.py`, pure check logic in `doctor.py`, info printer in `version.py`. Dashboard tree, console docs/screenshots, compose dashboard service, and dashboard-only tests are hard-deleted; remaining tests keep full library assertions.

**Tech Stack:** Python ≥3.12, `typer>=0.12`, `rich>=13`, `uv`, `pytest` (+`pytest-asyncio`), `ruff`, hatchling.

## Global Constraints

- Import path stays `iris` (`src/iris_ai/…`); do not rename the package.
- CLI surface is **only** `--help`, `version`, `doctor`, global `--version/-V`, `--debug`. No `chat` (or any other) stub.
- No network in any P1 command; `doctor` is offline and CI-safe.
- `doctor` never prints secret **values** — only key **names** and `set`/`missing`.
- Exit codes: `doctor` returns `1` only if a **fail** check fires; warnings exit `0`.
- No changes to agent graph, memory algorithms, JEV tools, Telegram bridge behavior, or `iris_ai.api` routes (except keeping already-uncommitted `_staged_preview` library work from Task 0).
- Do not weaken library test assertions to get green.
- Do not commit unless the user explicitly asks (global repo rule). Commit steps below are for when the user has approved execution and wants commits.
- Specs/docs under `docs/superpowers/specs/` are historical — do not rewrite them.
- Third-party hosts `console.groq.com` and `console.typesafe.ai` stay (they are not the dashboard).
- Plan source of truth: `docs/superpowers/specs/2026-09-22-p1-skeleton-library-cli-design.md`.

---

### Task 0: Triage uncommitted audit work

**Files:**
- Modify (keep): `src/iris_ai/api.py` (uncommitted `_staged_preview` / `staged` work)
- Modify (keep as base): `CHANGELOG.md`
- Create: `tests/test_staged_preview.py` (the two library tests relocated out of `tests/test_console_fixes.py` — only surviving coverage of `_staged_preview`)
- Discard: `dashboard/app.py`, `dashboard/static/app.js`, `dashboard/static/style.css`, `dashboard/templates/index.html` (deleted in Task 6 anyway)
- Delete (untracked): `console-live-dawn.png`, `console-live-mobile.png`, `console-live-night.png`, `docs/screenshots/console-audit-desktop.png`, `docs/screenshots/console-audit-mobile.png`, and `tests/test_console_fixes.py` **after** relocating the two library tests

**Interfaces:**
- Consumes: dirty worktree from the console audit pass
- Produces: worktree with only intentional survivors; later tasks assume dashboard file edits are gone

- [ ] **Step 1: Inspect status**

Run: `git status --short`  
Expected: modified `CHANGELOG.md`, `src/iris_ai/api.py`, four `dashboard/*` files; untracked console PNGs and `tests/test_console_fixes.py`.

- [ ] **Step 2: Confirm library survivor**

Run: `git diff src/iris_ai/api.py`  
Expected: `_staged_preview` + `staged` in `/mind` payload — keep this. If anything else appears, stop and ask the user.

- [ ] **Step 3: Discard dashboard-side edits (purposeful loss)**

```powershell
git checkout -- dashboard/app.py dashboard/static/app.js dashboard/static/style.css dashboard/templates/index.html
```

Expected: those four files no longer show as modified.

- [ ] **Step 4: Relocate library tests, then delete untracked audit artifacts**

`tests/test_console_fixes.py` mixes two `_staged_preview` library tests with three dashboard-pinning tests. Move the library pair first — deleting the file whole would drop the only coverage of `_staged_preview`.

Create `tests/test_staged_preview.py`:

```python
"""`iris_ai.api._staged_preview` — staged dream signals for the /mind payload."""

from __future__ import annotations

import json
from pathlib import Path

from iris_ai.api import _staged_preview
from iris_ai.memory.files import WorkspaceFiles


def test_staged_preview_reads_staging_jsonl(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    stage = files.staging_dir() / "staging-2026-09-22.jsonl"
    stage.write_text(
        "\n".join(
            [
                json.dumps({"content": "owner prefers dark mode", "importance": 7.5, "target": "MEMORY.md"}),
                "{not json",
                json.dumps({"content": "", "importance": 1}),
                json.dumps({"content": "renews lease in March", "importance": 6}),
            ]
        ),
        encoding="utf-8",
    )

    out = _staged_preview(files)

    assert [s["content"] for s in out] == [
        "owner prefers dark mode",
        "renews lease in March",
    ]
    assert out[0]["importance"] == 7.5


def test_staged_preview_empty_when_no_staging(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    assert _staged_preview(files) == []
```

Then delete the original file (its three remaining tests pin deleted dashboard HTML/JS/proxy):

```powershell
Remove-Item -LiteralPath console-live-dawn.png, console-live-mobile.png, console-live-night.png, tests/test_console_fixes.py -Force
Remove-Item -LiteralPath docs/screenshots/console-audit-desktop.png, docs/screenshots/console-audit-mobile.png -Force
```

Expected: `git status` shows only `CHANGELOG.md`, `src/iris_ai/api.py`, new untracked `tests/test_staged_preview.py`, plus plan/spec docs if untracked.

Sanity: `uv run pytest tests/test_staged_preview.py -q` → both PASS.

- [ ] **Step 5: Commit (only if user asked for commits)**

`tests/test_staged_preview.py` is untracked — it must be added explicitly:

```bash
git add src/iris_ai/api.py CHANGELOG.md tests/test_staged_preview.py
git commit -m "fix(api): expose staged dream signals in /mind"
```

Skip commit if the user has not requested it.

---

### Task 1: Dependencies, entry point, CLI package skeleton (TDD)

**Files:**
- Modify: `pyproject.toml` (deps, `[project.scripts]`, ruff `src`)
- Create: `src/iris_ai/cli/__init__.py`
- Create: `src/iris_ai/cli/main.py`
- Create: `src/iris_ai/cli/help_theme.py`
- Create: `src/iris_ai/cli/doctor.py`
- Create: `src/iris_ai/cli/version.py`
- Test: `tests/test_cli.py`
- Modify: `uv.lock` (via `uv lock`)

**Interfaces:**
- Consumes: `iris.__version__` (`str`, currently `"0.1.0"`); `iris_ai.config.settings` fields for key names (read-only presence checks — do not print values; `settings` fields with secrets already use `repr=False` where sensitive)
- Produces (later tasks + DoD rely on these exact names):
  - `iris_ai.cli.main:app` — `typer.Typer` instance (entry point)
  - `iris_ai.cli.doctor.run_checks(env_dir: Path | None = None, environ: Mapping[str, str] | None = None) -> list[Check]` (merges `.env` under process env for key-presence checks)
  - `iris_ai.cli.doctor._load_dotenv(env_path: Path) -> dict[str, str]` (internal; values never printed)
  - `iris_ai.cli.doctor.Check` — dataclass `name: str`, `level: Literal["ok", "warn", "fail"]`, `detail: str`
  - `iris_ai.cli.doctor.exit_code(checks: list[Check]) -> int` — `1` iff any `fail`, else `0`
  - `iris_ai.cli.version.version_lines() -> list[str]`
  - Console scripts: `iris = "iris_ai.cli.main:app"`

- [ ] **Step 1: Write failing tests**

Create `tests/test_cli.py`:

```python
"""P1 CLI surface: help, version, doctor — offline, no secret values."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from iris_ai import __version__
from iris_ai.cli.main import app

runner = CliRunner()

PROVIDER_KEYS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY")


def test_help_lists_only_real_commands():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "version" in result.stdout
    assert "doctor" in result.stdout
    for stub in ("chat", "ask", "run", "shell"):
        assert stub not in result.stdout


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_short_and_long_flag(flag: str):
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_version_output():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert f"iris {__version__}" in result.stdout
    assert "python" in result.stdout
    assert "package" in result.stdout


@pytest.mark.parametrize("flag", ["--version", "-V"])
def test_version_global_flags(flag: str):
    result = runner.invoke(app, [flag])
    assert result.exit_code == 0
    assert f"iris {__version__}" in result.stdout


def test_no_args_is_help():
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    assert "doctor" in result.stdout


def test_doctor_ok_and_never_prints_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    (tmp_path / ".env").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-value-xyz")
    monkeypatch.setenv("TYPESAFE_API_KEY", "jev-secret-abc")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "ok" in result.stdout
    assert "GEMINI_API_KEY" in result.stdout
    assert "test-secret-value-xyz" not in result.stdout
    assert "jev-secret-abc" not in result.stdout


def test_doctor_missing_env_is_warn_not_fail(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)  # no .env here
    for key in (*PROVIDER_KEYS, "TYPESAFE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "warn" in result.stdout
    assert "cp .env.example .env" in result.stdout


def test_doctor_sees_keys_only_in_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Quickstart keys live in .env — doctor must not warn "none set" when only .env has them."""
    monkeypatch.chdir(tmp_path)
    for key in (*PROVIDER_KEYS, "TYPESAFE_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    (tmp_path / ".env").write_text("GEMINI_API_KEY=from-dotenv-not-process-env\n", encoding="utf-8")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "GEMINI_API_KEY" in result.stdout
    assert "none set" not in result.stdout
    assert "from-dotenv-not-process-env" not in result.stdout


def test_doctor_crash_without_debug_prints_hint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Default mode: friendly error + hint, exit 1, no traceback."""
    import iris_ai.cli.doctor as doctor_mod

    def _boom():
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(doctor_mod, "run_checks", _boom)
    monkeypatch.delenv("IRIS_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "doctor crashed" in result.stdout
    assert "hint:" in result.stdout
    assert "--debug" in result.stdout


def test_doctor_crash_with_debug_flag_reraises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """--debug must re-raise the original exception so typer prints a traceback."""
    import iris_ai.cli.doctor as doctor_mod

    def _boom():
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(doctor_mod, "run_checks", _boom)
    monkeypatch.delenv("IRIS_DEBUG", raising=False)
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["--debug", "doctor"])
    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)
    assert "simulated crash" in str(result.exception)


def test_doctor_crash_with_iris_debug_env_reraises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import iris_ai.cli.doctor as doctor_mod

    def _boom():
        raise RuntimeError("simulated crash")

    monkeypatch.setattr(doctor_mod, "run_checks", _boom)
    monkeypatch.setenv("IRIS_DEBUG", "1")
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert isinstance(result.exception, RuntimeError)


def test_doctor_fail_exits_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, monkeypatch_import_fail):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1


@pytest.fixture
def monkeypatch_import_fail(monkeypatch: pytest.MonkeyPatch):
    """Force the package-import check to fail by breaking version helper import path."""
    import iris_ai.cli.doctor as doctor_mod

    def _boom():
        raise ImportError("simulated broken install")

    monkeypatch.setattr(doctor_mod, "_package_location", _boom)


def test_run_checks_levels_and_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from iris_ai.cli.doctor import exit_code, run_checks

    (tmp_path / ".env").write_text("", encoding="utf-8")
    monkeypatch.setenv("GEMINI_API_KEY", "set-but-hidden")
    monkeypatch.setenv("TYPESAFE_API_KEY", "")
    checks = run_checks(env_dir=tmp_path)
    names = {c.name for c in checks}
    assert ".env" in names
    assert "package" in names
    assert "provider keys" in names
    assert "TYPESAFE_API_KEY" in names
    blob = " | ".join(f"{c.name}:{c.detail}" for c in checks)
    assert "set-but-hidden" not in blob
    assert exit_code(checks) == 0
    from dataclasses import replace

    failing = [replace(checks[0], level="fail")]
    assert exit_code(failing) == 1


def test_version_lines_shape():
    from iris_ai.cli.version import version_lines

    lines = version_lines()
    assert lines[0].startswith("iris ")
    assert any(line.startswith("python ") for line in lines)
    assert any(line.startswith("package ") for line in lines)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -q`  
Expected: FAIL — `ModuleNotFoundError: No module named 'iris_ai.cli'` (or entry import error).

- [ ] **Step 3: Edit `pyproject.toml`**

Add `"typer>=0.12",` and `"rich>=13",` to `[project] dependencies`. Add:

```toml
[project.scripts]
iris = "iris_ai.cli.main:app"
```

Change ruff src line to:

```toml
src = ["src", "tests", "scripts", "mcp_servers"]
```

- [ ] **Step 4: Lock/sync**

Run: `uv lock && uv sync`  
Expected: success; typer and rich importable.

- [ ] **Step 5: Implement CLI package**

`src/iris_ai/cli/__init__.py`:

```python
"""Iris command-line interface."""
```

`src/iris_ai/cli/help_theme.py`:

```python
"""Rich styling for the CLI. Plain output when piped or NO_COLOR is set."""

from __future__ import annotations

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "iris.ok": "bold green",
        "iris.warn": "bold yellow",
        "iris.fail": "bold red",
        "iris.title": "bold cyan",
    }
)


def console(stderr: bool = False) -> Console:
    return Console(theme=THEME, stderr=stderr)


LEVEL_STYLE = {"ok": "iris.ok", "warn": "iris.warn", "fail": "iris.fail"}
```

`src/iris_ai/cli/version.py`:

```python
"""`iris version` — version, interpreter, install location."""

from __future__ import annotations

import sys
from pathlib import Path

import iris_ai


def _package_location() -> Path:
    return Path(iris.__file__).resolve().parent


def version_lines() -> list[str]:
    return [
        f"iris {iris.__version__}",
        f"python {sys.version.split()[0]}",
        f"package  {_package_location()}",
    ]


def print_version() -> None:
    for line in version_lines():
        print(line)
```

`src/iris_ai/cli/doctor.py`:

```python
"""`iris doctor` — offline environment checks. Never prints secret values."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

PROVIDER_KEY_NAMES = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY")


@dataclass(frozen=True)
class Check:
    name: str
    level: Literal["ok", "warn", "fail"]
    detail: str


def _package_location() -> Path:
    import iris_ai

    return Path(iris.__file__).resolve().parent


def _load_dotenv(env_path: Path) -> dict[str, str]:
    """Minimal .env parser: KEY=value lines, # comments, no interpolation.

    Only used for presence checks — values are never printed.
    """
    out: dict[str, str] = {}
    try:
        text = env_path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip().strip('"').strip("'")
    return out


def run_checks(env_dir: Path | None = None, environ: Mapping[str, str] | None = None) -> list[Check]:
    root = Path.cwd() if env_dir is None else env_dir
    checks: list[Check] = []

    env_path = root / ".env"
    file_vars: dict[str, str] = {}
    if not env_path.exists():
        checks.append(Check(".env", "warn", "missing — cp .env.example .env"))
    else:
        try:
            env_path.read_text(encoding="utf-8")
            file_vars = _load_dotenv(env_path)
            checks.append(Check(".env", "ok", "present"))
        except OSError:
            checks.append(Check(".env", "fail", "unreadable"))

    # Process env wins; .env fills gaps so quickstart keys are visible.
    base = dict(os.environ if environ is None else environ)
    env: dict[str, str] = {**file_vars, **base}

    try:
        loc = _package_location()
        checks.append(Check("package", "ok", f"importable at {loc}"))
    except Exception:
        checks.append(Check("package", "fail", "cannot import iris_ai"))

    present = [name for name in PROVIDER_KEY_NAMES if env.get(name, "").strip()]
    if present:
        checks.append(Check("provider keys", "ok", ", ".join(present)))
    else:
        checks.append(Check("provider keys", "warn", "none set — set a provider key"))

    if env.get("TYPESAFE_API_KEY", "").strip():
        checks.append(Check("TYPESAFE_API_KEY", "ok", "set"))
    else:
        checks.append(Check("TYPESAFE_API_KEY", "warn", "missing — JEV disabled (deterministic fallback)"))

    return checks


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.level == "fail" for c in checks) else 0


def render(checks: list[Check]) -> None:
    from iris_ai.cli.help_theme import LEVEL_STYLE, console

    out = console()
    out.print("[iris.title]Iris doctor[/iris.title]")
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for c in checks:
        counts[c.level] += 1
        style = LEVEL_STYLE[c.level]
        out.print(f"  [{style}]{c.level:4}[/{style}] {c.name}: {c.detail}")
    out.print(f"{counts['ok']} ok · {counts['warn']} warn · {counts['fail']} fail")
```

`src/iris_ai/cli/main.py`:

```python
"""Iris CLI root — typer app bound to [project.scripts] iris."""

from __future__ import annotations

import os

import typer

from iris_ai.cli import doctor as doctor_mod
from iris_ai.cli import version as version_mod
from iris_ai.cli.help_theme import console

app = typer.Typer(
    name="iris",
    help="Iris — personal agent harness (library + CLI).",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

# Written by the root callback; read by commands that need --debug state.
# click only guarantees root params exist during callback execution, so we
# stash the resolved flag instead of reading it lazily inside subcommands.
_debug_flag = False


def _debug_enabled(debug: bool) -> bool:
    return debug or os.environ.get("IRIS_DEBUG", "") == "1"


def _fail(message: str, debug: bool, *, cause: BaseException | None = None) -> None:
    """Print a friendly error; under debug re-raise `cause` for a traceback."""
    err = console(stderr=True)
    err.print(f"[iris.fail]error[/iris.fail] {message}")
    if debug:
        if cause is not None:
            raise cause  # original traceback (spec: --debug / IRIS_DEBUG=1)
        raise typer.Exit(code=1)
    err.print("hint: re-run with --debug or IRIS_DEBUG=1 for a traceback")
    raise typer.Exit(code=1)


@app.callback()
def root(
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit.", is_eager=True
    ),
    debug: bool = typer.Option(False, "--debug", help="Full tracebacks on error."),
) -> None:
    global _debug_flag
    _debug_flag = _debug_enabled(debug)
    if version:
        version_mod.print_version()
        raise typer.Exit()


@app.command()
def version() -> None:
    """Print version, Python, and install location."""
    version_mod.print_version()


@app.command()
def doctor() -> None:
    """Offline environment checks (names only — never secret values)."""
    try:
        checks = doctor_mod.run_checks()
    except Exception as exc:
        _fail(f"doctor crashed: {exc}", _debug_flag, cause=exc)
        return
    doctor_mod.render(checks)
    raise typer.Exit(code=doctor_mod.exit_code(checks))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run CLI tests to green**

Run: `uv sync && uv run pytest tests/test_cli.py -q`  
Expected: all PASS.

- [ ] **Step 7: Manual smoke**

```powershell
uv run iris --help
uv run iris version
uv run iris doctor
```

Expected: styled help with only `version` + `doctor`; version three lines; doctor aligned ok/warn/fail + summary; no traceback; exit 0 (warn ok). If `.env` missing locally, still exit 0.

- [ ] **Step 8: Commit (only if user asked)**

```bash
git add pyproject.toml uv.lock src/iris_ai/cli tests/test_cli.py
git commit -m "feat(cli): iris help/version/doctor (typer + rich, offline)"
```

(All paths above are tracked-or-intentionally-new under `src/`/`tests/`; `git add` of the new `src/iris_ai/cli` directory stages the whole package.)

---

### Task 2: Test migration

**Files:**
- Confirm gone: `tests/test_console_fixes.py` (deleted in Task 0 after the two `_staged_preview` tests moved to `tests/test_staged_preview.py`)
- Modify: `tests/test_security.py` (docstring + remove dashboard proxy test only)

**Interfaces:**
- Consumes: Task 0 (dashboard edits discarded, staged-preview tests relocated), dashboard still on disk until Task 3
- Produces: test suite with zero `dashboard.app` imports; bridge + iris-core auth tests unchanged

- [ ] **Step 1: Confirm dashboard-only test file is gone**

```powershell
Test-Path tests/test_console_fixes.py   # must be False
uv run pytest tests/test_staged_preview.py -q   # both PASS — coverage preserved
```

Expected: file absent; relocated tests green. Reason (for CHANGELOG): dashboard UI removed in P1; the three remaining tests pinned deleted HTML/JS/proxy (the two `_staged_preview` tests now live in `tests/test_staged_preview.py`).

- [ ] **Step 2: Edit `tests/test_security.py`**

Replace the module docstring with:

```python
"""Bearer-token auth tests: iris-core enforcement + bridge forwarding.

iris-core routes are tested by introspection (no lifespan, no Postgres):
every APIRoute except /health must declare the require_token dependency.
Forwarding is tested with in-memory httpx transports.
"""
```

Delete the entire function `test_dashboard_proxy_forwards_token` (from `async def test_dashboard_proxy_forwards_token` through its final assert). Keep `HeaderProbeTransport`, both bridge tests, and all `require_token` tests.

- [ ] **Step 3: Verify no dashboard imports in tests**

Run: `rg -n "dashboard" tests`  
Expected: only `tests/test_ingest.py` URL fixture `"http://dashboard:8080/x"` (keep — not an import). Optionally rename that host to `http://internal:8080/x` for clarity; not required.

- [ ] **Step 4: Run migrated tests**

Run: `uv run pytest tests/test_security.py -q`  
Expected: PASS (fewer tests than before — one deleted on purpose).

- [ ] **Step 5: Commit (only if user asked)**

```bash
git add tests/test_security.py
git commit -m "test: drop dashboard proxy coverage; keep core + bridge auth"
```

---

### Task 3: Hard deletes + compose/CI/env cleanup

**Files:**
- Delete tree: `dashboard/` (app, static, templates, Dockerfile, requirements, pycache)
- Delete: `docs/console.md`
- Delete: `docs/screenshots/console-dawn.png`, `console-night.png`, `console-mobile.png` (and any remaining `console-*.png`)
- Delete if present: root `console-live-*.png`, `dashboard.log`, `dashboard-err.log`
- Modify: `docker-compose.yml` (remove `dashboard` service; fix iris-core port comment)
- Modify: `.github/workflows/ci.yml` (remove `DASHBOARD_USER` / `DASHBOARD_PASSWORD`)
- Modify: `.env.example` (remove dashboard auth block; fix “dashboard” wording lines 59, 121)
- Modify: `docs/deployment.md` (remove dashboard env row)
- Check: `Dockerfile`, `.dockerignore` for `dashboard` references

**Interfaces:**
- Consumes: Tasks 0–2 (tests no longer import dashboard)
- Produces: `dashboard/` absent; compose has `postgres`, `iris-core`, `telegram-mcp` only; CI env has no dashboard vars

- [ ] **Step 1: Delete dashboard tree and console artifacts**

```powershell
git rm -r dashboard
git rm docs/console.md
Get-ChildItem docs/screenshots -Filter 'console-*.png' -ErrorAction SilentlyContinue | ForEach-Object { git rm $_.FullName }
Remove-Item -Path console-live-*.png, dashboard.log, dashboard-err.log -Force -ErrorAction SilentlyContinue
```

Expected: `Test-Path dashboard` → `False`.

- [ ] **Step 2: Remove compose dashboard service**

In `docker-compose.yml`, delete lines from `  dashboard:` through the last healthcheck line before `volumes:` (the service block ending just before `volumes:`). Update iris-core comment:

```yaml
    ports:
      # Internal service: the Telegram bridge and local tools call it.
      # Bound to loopback so `docker compose up` never exposes an
      # auth-disabled API to the network.
      - "127.0.0.1:8000:8000"
```

Expected: `docker compose config -q` parses (or visual check: only three services + volumes).

- [ ] **Step 3: Strip dashboard CI env**

In `.github/workflows/ci.yml`, delete:

```yaml
  DASHBOARD_USER: ci
  DASHBOARD_PASSWORD: ci
```

- [ ] **Step 4: Fix `.env.example`**

Delete the block from `# ── Dashboard auth (HTTP Basic) ─` through `DASHBOARD_PASSWORD=` (end of file section). Adjust:

- Line ~59: `… the Telegram bridge and dashboard` → `… the Telegram bridge`
- Line ~121: `rendered in the console's judgment panel` → `kept for turn analysis (/mind, CLI later)`

Do not touch `console.groq.com` / `console.typesafe.ai` URLs.

- [ ] **Step 5: Fix `docs/deployment.md`**

Remove the row `| DASHBOARD_USER / DASHBOARD_PASSWORD | … |` and any “dashboard” service mentions; renumber if the table is sequential.

- [ ] **Step 6: Sweep config/docs**

Run:
```bash
rg -n "dashboard" docker-compose.yml .github .env.example docs/deployment.md Dockerfile .dockerignore pyproject.toml
uv run ruff check .
```
Expected: no matches in those files; ruff clean.

- [ ] **Step 7: Commit (only if user asked)**

```bash
git add -A docker-compose.yml .github .env.example docs/deployment.md Dockerfile .dockerignore
git commit -m "chore: remove dashboard service, env vars, and console artifacts"
```

(Task 3 deletions may already be staged from `git rm` — include them in this or a paired `git rm` commit.)

---

### Task 4: Full suite + leftover grep sweep

**Files:**
- Possibly fix: README (moved to Task 5 if large), stray comments in `src/iris_ai/**` mentioning “dashboard”
- Modify: none required if Tasks 1–3 done

**Interfaces:**
- Consumes: Tasks 1–3
- Produces: green ruff + pytest; pass count recorded for CHANGELOG

- [ ] **Step 1: Lint + full tests**

```powershell
uv run ruff check .
uv run pytest tests -q
```
Expected: ruff clean; all tests PASS. Record the exact pass count (e.g. `N passed`).

- [ ] **Step 2: Grep sweep**

```bash
rg -ni "dashboard|docs/console|console-live|uvicorn dashboard" \
  --glob '!docs/superpowers/specs/**' \
  --glob '!docs/superpowers/plans/**' \
  --glob '!CHANGELOG.md' \
  --glob '!**/.venv/**' \
  --glob '!**/__pycache__/**' \
  --glob '!uv.lock'
```

Allowed: `console.groq.com`, `console.typesafe.ai`. Fix other hits (README left for Task 5; cheap `src/iris_ai` comment wording fixes OK here).

- [ ] **Step 3: Commit (only if user asked)**

Only if Step 2 changed files:

```bash
git add -u
git commit -m "docs: drop dashboard wording from library comments"
```

---

### Task 5: README rewrite + CHANGELOG entry

**Files:**
- Rewrite: `README.md`
- Modify: `CHANGELOG.md` (new P1 section at top; fold/drop console Unreleased bullets that only described the deleted UI)

**Interfaces:**
- Consumes: final test count from Task 4; CLI commands from Task 1
- Produces: README quickstart that matches DoD; CHANGELOG lists removals + test count

- [ ] **Step 1: Rewrite README to spec §8 outline**

Target structure (actual prose, no placeholders):

1. Title + one-paragraph pitch: Iris is a **library + CLI** personal agent harness; Telegram, computer-use, multi-agent, cron UI = later phases (table P1–P8, one line each).
2. **Status: P1** — `version` + `doctor` only.
3. Quickstart:
   ```bash
   uv sync
   uv run iris --help
   uv run iris doctor
   ```
   plus provider keys via `cp .env.example .env`.
4. Keep true sections: memory model (Markdown soul + pgvector), turn pipeline mermaid **without** dashboard node, JEV, ablation link.
5. Remove: console bullet, `### The console`, all `docs/console.md` links, `uvicorn dashboard.app`, `:8080` dashboard instructions, `dashboard:8080` compose diagram rows, screenshot images of the console, docs index row for console.
6. Architecture/compose section: services = postgres, iris-core, telegram-mcp.
7. Link `docs/superpowers/specs/` for phase specs.

- [ ] **Step 2: CHANGELOG P1 section**

Prepend (adjust N to Task 4 count; fold any still-true library bullets from old Unreleased sections, delete pure-dashboard bullets):

```markdown
## Unreleased — P1: library-first skeleton + CLI

### Added
- CLI: `iris --help`, `iris version`, `iris doctor` (typer + rich); `[project.scripts]` entry point.
- Offline doctor checks: `.env` (file + process env), package import, provider key names, `TYPESAFE_API_KEY` — never secret values; exit 1 only on fail; `--debug` / `IRIS_DEBUG=1` re-raises for a traceback.

### Removed
- Web dashboard (`dashboard/`), `docs/console.md`, console screenshots/logs.
- docker-compose `dashboard` service; `DASHBOARD_USER` / `DASHBOARD_PASSWORD` from `.env.example` and CI.
- `tests/test_console_fixes.py` (pinned deleted dashboard HTML/JS/proxy; the two `_staged_preview` library tests moved to `tests/test_staged_preview.py`).

### Changed
- `tests/test_security.py`: dropped `dashboard.app` proxy test; kept iris-core route auth + Telegram bridge forwarding.
- README rewritten for library + CLI shape with P1–P8 roadmap.
- Dependencies: +`typer>=0.12`, +`rich>=13`. Ruff `src` no longer includes `dashboard`.

### Tests
- Final count: **N** (all green; dashboard-only tests deleted with reasons above).
```

- [ ] **Step 3: Verify README claims**

Run:
```bash
rg -n "dashboard|docs/console|uvicorn dashboard|:8080" README.md
uv run iris --help
uv run iris doctor
```
Expected: no dashboard hits (except none); CLI matches quickstart.

- [ ] **Step 4: Commit (only if user asked)**

```bash
git add README.md CHANGELOG.md
git commit -m "docs: README + CHANGELOG for P1 library-first CLI shape"
```

---

### Task 6: DoD verification (hand to user)

**Files:** none (verification only)

**Interfaces:**
- Consumes: Tasks 0–5
- Produces: user-checked DoD; **gate for any P2 work**

- [ ] **Step 1: Run the spec DoD checklist**

```powershell
uv sync
uv run iris --help
uv run iris doctor
uv run iris version
Test-Path dashboard    # must be False
uv run ruff check .
uv run pytest tests -q
```

Expected:
- help: styled, commands = version + doctor only (`-h` / `--help`), no traceback
- doctor: names only (`.env` + process env), exit 0 unless fail; `--debug` / `IRIS_DEBUG=1` yields a real traceback on crash
- doctor: names only, exit 0 unless fail
- version: `iris <ver>`, python, package path
- dashboard absent
- ruff clean
- pytest all green; count matches CHANGELOG

- [ ] **Step 2: Present checklist to user**

Paste outputs; ask user to tick every box in spec §2 DoD **and** confirm README quickstart (`clone → uv sync → uv run iris --help`).

- [ ] **Step 3: Stop — no P2 until user approves**

Do not start core-brain/chat work. Spec gate: “No P2 work until the user marks P1 complete and verified.”

---

## Out of scope (reject during review if seen)

- `iris chat` or any command beyond the DoD list
- Agent/memory/JEV/Telegram behavior changes
- Textual TUI, PyPI publish, package rename
- Postgres connectivity check in `doctor`
- Freezing public API beyond `__version__`
- Rewriting historical specs under `docs/superpowers/specs/`

## Risk checklist (spec §9)

| Risk | Covered by |
|---|---|
| CI/compose still need `dashboard/` | Task 3 Step 6 sweep |
| Tests silently dropped | Task 2 + Task 4 count + CHANGELOG |
| Fake CLI commands | Task 1 test `test_help_lists_only_real_commands` |
| Secrets leak via doctor | Task 1 `test_doctor_ok_and_never_prints_secrets` + `run_checks` unit test |
| `_staged_preview` coverage dropped with dashboard tests | Task 0 Step 4 relocates both tests to `tests/test_staged_preview.py` |
| `--debug` never produces a traceback | Task 1 `_fail(..., cause=…)` re-raises; `test_doctor_crash_*` pins both modes |
| Keys only in `.env` misreported as missing | Task 1 `doctor` parses `.env` under process env; `test_doctor_sees_keys_only_in_dotenv` |
| Sibling plan docs reintroduce "dashboard" matches | Task 4 grep sweep excludes `docs/superpowers/plans/**` |
