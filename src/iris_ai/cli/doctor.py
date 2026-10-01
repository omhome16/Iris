"""`iris doctor` — offline environment checks. Never prints secret values."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from iris_ai.providers import AUTO_ORDER, KEYED_PROVIDERS, PROVIDERS

# Every key that can carry a model-provider credential, straight from the
# registry — so adding a provider does not leave `doctor` silently blind to it.
PROVIDER_KEY_NAMES = tuple(p.key_env for p in KEYED_PROVIDERS)

# Model ids that used to be defaults and now 404. A checkout that copied them
# into .env keeps failing over on every Groq call, because .env beats the
# fixed defaults in config.py. Doctor names them; it never rewrites .env.
DEAD_MODEL_IDS = (
    "qwen/qwen3.6-27b",
    "groq/qwen/qwen3.6-27b",
    "groq/compound-mini",
    "groq/groq/compound-mini",
)


def stale_model_checks(env: Mapping[str, str]) -> list[Check]:
    """Warn when a configured model id is one we already know is dead."""
    found: list[str] = []
    for key, value in env.items():
        if not key.endswith("_MODEL"):
            continue
        model = value.strip()
        if model in DEAD_MODEL_IDS:
            found.append(f"{key}={model}")
    if not found:
        return []
    listed = ", ".join(found)
    return [
        Check(
            "model ids",
            "warn",
            f"dead model id in config ({listed}). Remove the line so the current default is used.",
            fix=cli_cmd("config", "model"),
        )
    ]


def _resolve_provider(requested: str, env: Mapping[str, str]) -> str:
    """Which provider a turn would actually use, from env alone.

    Deliberately mirrors `Settings._autodetect_provider` without constructing
    Settings: doctor must stay offline and must not raise on a malformed .env.
    """
    if requested != "auto" and requested in PROVIDERS:
        return requested
    for name in AUTO_ORDER:
        spec = PROVIDERS[name]
        if spec.key_env and env.get(spec.key_env, "").strip():
            return name
    return "ollama"


@dataclass(frozen=True)
class Check:
    name: str
    level: Literal["ok", "warn", "fail", "opt"]
    detail: str
    fix: str = ""


def cli_cmd(*parts: str) -> str:
    """How to invoke iris from this environment.

    A clone does not put `iris` on PATH. Doctor used to print `iris doctor`,
    which then failed with "not recognized".
    """
    import shutil

    tool = "iris" if shutil.which("iris") else "uv run iris"
    return " ".join((tool, *parts))


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
        checks.append(
            Check(".env", "warn", "missing — cp .env.example .env", fix=cli_cmd("doctor", "--fix"))
        )
    else:
        try:
            env_path.read_text(encoding="utf-8")
            file_vars = _load_dotenv(env_path)
            checks.append(Check(".env", "ok", "present"))
        except OSError:
            checks.append(Check(".env", "fail", "unreadable", fix="iris init"))

    # Process env wins; .env fills gaps so quickstart keys are visible.
    base = dict(os.environ if environ is None else environ)
    env: dict[str, str] = {**file_vars, **base}

    try:
        loc = _package_location()
        checks.append(Check("package", "ok", f"importable at {loc}"))
    except Exception:  # noqa: BLE001 — any import failure is a fail-level check result
        checks.append(Check("package", "fail", "cannot import iris_ai", fix="uv sync"))

    present = [name for name in PROVIDER_KEY_NAMES if env.get(name, "").strip()]
    if present:
        checks.append(Check("provider keys", "ok", ", ".join(present)))
    else:
        checks.append(Check("provider keys", "warn", "none set — set a provider key", fix=cli_cmd("init")))

    requested = (env.get("LLM_PROVIDER", "auto") or "auto").strip().lower()
    resolved = _resolve_provider(requested, env)
    checks.append(Check("model provider", "ok", f"{PROVIDERS[resolved].label} (LLM_PROVIDER={requested})"))
    checks.extend(stale_model_checks(env))
    if requested not in ("auto",) and requested not in PROVIDERS:
        checks.append(
            Check(
                "LLM_PROVIDER",
                "warn",
                f"{requested!r} is not a known provider — falling back to {resolved}",
                fix=cli_cmd("config", "provider"),
            )
        )
    # A provider can only be used once a model id exists for it. The registry
    # names the field, so the env var to look for is derived rather than listed.
    spec = PROVIDERS[resolved]
    model_env = spec.strong_field.upper()
    if spec.requires_model_config and not env.get(model_env, "").strip():
        checks.append(
            Check(
                "strong model",
                "warn",
                f"{model_env} is unset — {spec.label} cannot be used yet",
                fix=cli_cmd("config", "model"),
            )
        )

    if env.get("TYPESAFE_API_KEY", "").strip():
        checks.append(Check("TYPESAFE_API_KEY", "ok", "set"))
    else:
        checks.append(
            Check(
                "TYPESAFE_API_KEY",
                "opt",
                "optional — JEV stays on the deterministic fallback until a key is set",
                fix=cli_cmd("config", "extras"),
            )
        )

    checks.append(_secret_store_check(env))
    return checks


def _secret_store_check(env: Mapping[str, str]) -> Check:
    """What holds the secrets, and whether the choice is a weak one.

    Names and locations only — the rule for this whole command. The `file`
    backend is a `warn` rather than an `ok` because it is real isolation without
    encryption, and an operator should know that from the doctor rather than from
    the module docstring.
    """
    from iris_ai.secrets import available_backends

    requested = (env.get("SECRET_STORE", "auto") or "auto").strip().lower()
    if requested not in ("auto", "env", "keyring", "file"):
        return Check(
            "secret store",
            "fail",
            f"SECRET_STORE={requested!r} is not a known backend",
            fix="iris secrets backend",
        )
    chosen = requested
    if requested == "auto":
        chosen = "keyring" if available_backends()["keyring"] else "file"
    if chosen == "keyring":
        return Check("secret store", "ok", "keyring (OS-encrypted)")
    if chosen == "env":
        return Check("secret store", "ok", "env (read-only; nothing stored by Iris)")
    return Check(
        "secret store",
        "opt",
        "file (0600, not encrypted). Optional: `uv sync --extra secrets` then the OS keychain.",
        fix=cli_cmd("secrets", "backend"),
    )


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.level == "fail" for c in checks) else 0


def apply_safe_fixes(env_dir: Path | None = None) -> list[str]:
    """Fixes that cannot destroy a config: create a missing `.env` from the sample."""
    root = Path.cwd() if env_dir is None else env_dir
    done: list[str] = []
    env_path = root / ".env"
    example = root / ".env.example"
    if not env_path.exists() and example.is_file():
        env_path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
        done.append("created .env from .env.example")
    return done


def render(checks: list[Check]) -> None:
    from iris_ai.cli import ui
    from iris_ai.cli.help_theme import console

    out = console()
    ui.header(out, "Iris doctor", "offline checks - names only, never secret values")
    ui.section(out, "environment")
    for check in checks:
        detail = check.detail if not check.fix or check.level == "ok" else f"{check.detail}  fix: {check.fix}"
        ui.status(out, check.level, check.name, detail)
    out.print()
    ui.counts(out, [check.level for check in checks])
