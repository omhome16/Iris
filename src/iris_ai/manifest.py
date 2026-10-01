"""Optional harness manifest — declarative, non-secret configuration.

`config/harness.toml` lets a user set the knobs they would otherwise put in
`.env`, in one readable file. Precedence is:

    defaults  <  .env  <  manifest  <  environment  <  CLI flag

so a real environment variable always wins over the file — a container or CI
override still works — while `.env`, which is where a copied sample's defaults
live, sits *below* the manifest: otherwise a line `.env.example` shipped
(`WORKSPACE_DIR=./workspace`) would silently defeat a profile that names its own
workspace, which is exactly what happened the first time
`examples/assistant/harness.toml` was run. `PRELOADED_ENV` is what draws the
line: a variable that was in the environment before Iris imported anything is
the environment, and one that a dependency's `load_dotenv()` introduced is the
file. The manifest is meant to be **committable**; secrets never belong here,
they stay in `.env`.

Only keys naming a real ``Settings`` field are applied. An unknown key is logged
and ignored rather than silently changing nothing, so a typo is visible. The
``[channels]`` section is the one ergonomic convenience: it maps lists onto the
string-valued ``channels_enabled`` / ``channels_disabled`` settings.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path
from typing import Any

log = logging.getLogger("iris.manifest")


def load_manifest(path: Path) -> dict[str, Any]:
    """Read a TOML manifest. Missing/unreadable is an empty manifest, not an error."""
    if not path.is_file():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except Exception as exc:  # noqa: BLE001 - a bad manifest must not stop boot
        log.warning("ignoring unreadable harness manifest %s: %s", path, exc)
        return {}


def _join(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    return str(value)


def _coerce(current: Any, value: Any) -> Any:
    """Cast a manifest value to the type the setting already holds.

    Pydantic validates values that arrive through the environment; one written
    directly onto the model does not get that, so the cast happens here against
    the current attribute's type.
    """
    if isinstance(current, bool):
        return bool(value)
    if isinstance(current, int) and not isinstance(current, bool):
        return int(value)
    if isinstance(current, float):
        return float(value)
    return value if isinstance(value, str) else _join(value)


def environment_overrides() -> dict[str, str]:
    """The environment variables that are genuinely the environment.

    Keys that appeared only because a dependency loaded `.env` are dropped, so
    "the environment wins" keeps meaning a real override rather than a line the
    sample file shipped. See `iris_ai.PRELOADED_ENV` for the reasoning.
    """
    from iris_ai import PRELOADED_ENV

    return {key: value for key, value in os.environ.items() if key in PRELOADED_ENV}


def apply_manifest(settings: Any, data: dict[str, Any], *, environ: dict | None = None) -> list[str]:
    """Apply a loaded manifest to ``settings``; returns the names applied.

    A key present in the *real* environment is left untouched (env wins). Unknown
    keys are logged. Only scalar top-level keys and the ``[channels]`` table are
    read.
    """
    environ = environment_overrides() if environ is None else environ
    flat: dict[str, Any] = {
        key: value for key, value in data.items() if not isinstance(value, dict)
    }
    channels = data.get("channels")
    if isinstance(channels, dict):
        if "enabled" in channels:
            flat["channels_enabled"] = _join(channels["enabled"])
        if "disabled" in channels:
            flat["channels_disabled"] = _join(channels["disabled"])
    agents = data.get("agents")
    if isinstance(agents, dict):
        if "max_parallel" in agents:
            flat["multi_agent_max_parallel"] = agents["max_parallel"]
        if "max_calls_per_turn" in agents:
            flat["multi_agent_max_calls"] = agents["max_calls_per_turn"]
        if "budget_usd" in agents:
            flat["agents_budget_usd"] = agents["budget_usd"]

    fields = getattr(type(settings), "model_fields", None) or getattr(settings, "model_fields", {})
    applied: list[str] = []
    for key, value in flat.items():
        name = key.lower()
        if name not in fields:
            log.warning("harness manifest: unknown setting %r (ignored)", key)
            continue
        if name.upper() in environ:
            log.debug("harness manifest: %s overridden by environment", name)
            continue
        setattr(settings, name, _coerce(getattr(settings, name, None), value))
        applied.append(name)
    return applied
