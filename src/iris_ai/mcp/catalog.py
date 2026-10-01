"""The built-in MCP catalog. Presets a person can switch on by name."""

from __future__ import annotations

import tomllib
from pathlib import Path


def load_catalog() -> dict[str, dict]:
    path = Path(__file__).with_name("catalog.toml")
    if not path.is_file():
        return {}
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return {str(name): body for name, body in data.items() if isinstance(body, dict)}


def preset(name: str) -> dict | None:
    return load_catalog().get(name)
