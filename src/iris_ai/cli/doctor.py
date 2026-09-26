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

    return Path(iris_ai.__file__).resolve().parent


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
    except Exception:  # noqa: BLE001 — any import failure is a fail-level check result
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
