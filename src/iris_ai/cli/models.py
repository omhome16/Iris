"""Switch the model in use, and probe it. The key itself is never printed."""

from __future__ import annotations

import asyncio
from pathlib import Path

from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.cli.toml_edit import upsert
from iris_ai.config import settings
from iris_ai.providers import PROVIDERS


def manifest_path() -> Path:
    return Path(settings.harness_config)


def switch_provider(name: str) -> str:
    """Point the process and harness.toml at a provider. Returns a status line."""
    key = name.strip().lower()
    spec = PROVIDERS.get(key)
    if spec is None:
        known = ", ".join(PROVIDERS)
        return f"unknown provider {name!r}. known: {known}"
    settings.llm_provider = key
    model = (getattr(settings, spec.strong_field, "") or "").strip()
    if model:
        settings.strong_model = model
        settings._models_strong[key] = model
    settings._resolved_provider = key
    upsert(manifest_path(), "llm_provider", key)
    return f"{spec.label} / {model or '(set a model with /model)'}"


def switch_model(model_id: str) -> str:
    """Set the strong model for the provider in use, and remember it."""
    model = model_id.strip()
    if not model:
        return "usage: /model <id>"
    provider = getattr(settings, "_resolved_provider", None) or settings.llm_provider
    spec = PROVIDERS.get(str(provider).strip().lower())
    field = spec.strong_field if spec is not None else "strong_model"
    setattr(settings, field, model)
    settings.strong_model = model
    if hasattr(settings, "_models_strong") and spec is not None:
        settings._models_strong[spec.name] = model
    upsert(manifest_path(), field, model)
    return model


async def probe(model: str | None = None) -> tuple[bool, str]:
    """One short completion. The return text never includes a key."""
    from iris_ai.capabilities.models import MODELS

    target = (model or settings.strong_model or settings.cheap_model or "").strip()
    if not target:
        return False, "no model id is set"
    try:
        llm = MODELS.build(settings.model_backend, ledger=None)
        text = await asyncio.wait_for(
            llm.complete([{"role": "user", "content": "Reply with the single word ok."}]),
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 - the probe reports the failure
        return False, f"{type(exc).__name__}: {exc}"
    shown = str(text or "").strip().splitlines()[0][:80]
    return True, f"{target} replied: {shown or '(empty)'}"


def run(action: str = "test", model: str = "") -> int:
    out = console()
    if action not in {"test", "probe"}:
        ui.failed(out, "usage:", "iris models test")
        return 2
    ok, detail = asyncio.run(probe(model or None))
    if ok:
        ui.status(out, "ok", "model", detail)
        return 0
    ui.status(out, "fail", "model", detail)
    ui.note(out, "fix: iris config model")
    return 1
