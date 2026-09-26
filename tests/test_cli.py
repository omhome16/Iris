"""P1 CLI surface: help, version, doctor — offline, no secret values."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from typer.testing import CliRunner

from iris import __version__
from iris.cli.main import app

ROOT = Path(__file__).resolve().parents[1]

runner = CliRunner()

PROVIDER_KEYS = ("GEMINI_API_KEY", "OPENROUTER_API_KEY", "GROQ_API_KEY")


def test_help_lists_only_real_commands():
    """The registry *is* the allowed set: a stub cannot sneak in unnoticed.

    `chat` became real in P2, `skills` in P4 and `tools` in P7, so each moved
    from the forbidden list to the required one; the assertion that matters is
    that the set is exact.
    """
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    names = {(cmd.name or cmd.callback.__name__) for cmd in app.registered_commands}
    assert names == {"agents", "chat", "cron", "doctor", "guards", "skills", "tools", "version"}
    for name in names:
        assert name in result.stdout
    for stub in ("ask", "run", "shell", "serve", "tui"):
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


def _runtime_strings(path: Path) -> list[tuple[int, str]]:
    """String literals that can reach a console — docstrings excluded, because a
    docstring is never printed."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    scopes = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, scopes) or not node.body:
            continue
        first = node.body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_cli_text_avoids_characters_a_windows_console_cannot_encode():
    """On Windows a console falls back to cp1252, and a character outside it does
    not degrade to a box — rich switches to the legacy renderer and the command
    dies with `UnicodeEncodeError`.

    `iris guards` shipped a `↑` that did exactly that. The rule is asserted over
    the CLI's own literals rather than over captured output, because rich owns
    its border characters and downgrades those itself; what it cannot downgrade
    is a character *we* wrote.
    """
    offenders: list[str] = []
    seen = 0
    for path in sorted((ROOT / "src" / "iris" / "cli").glob("*.py")):
        for lineno, value in _runtime_strings(path):
            seen += 1
            for char in value:
                try:
                    char.encode("cp1252")
                except UnicodeEncodeError:
                    offenders.append(f"{path.name}:{lineno} {char!r} ({hex(ord(char))})")
    assert seen > 200, "the walker stopped finding literals — fix it, do not trust a pass"
    assert not offenders, "not printable on a Windows console:" + ", ".join(offenders)


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
    import iris.cli.doctor as doctor_mod

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
    import iris.cli.doctor as doctor_mod

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
    import iris.cli.doctor as doctor_mod

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
    import iris.cli.doctor as doctor_mod

    def _boom():
        raise ImportError("simulated broken install")

    monkeypatch.setattr(doctor_mod, "_package_location", _boom)


def test_run_checks_levels_and_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from iris.cli.doctor import exit_code, run_checks

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
    from iris.cli.version import version_lines

    lines = version_lines()
    assert lines[0].startswith("iris ")
    assert any(line.startswith("python ") for line in lines)
    assert any(line.startswith("package ") for line in lines)
