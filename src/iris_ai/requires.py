"""`requires = ["iris>=0.4,<1.0"]` against the installed version."""

from __future__ import annotations

import re

from iris_ai import __version__

_SPEC = re.compile(r"^iris\s*(>=|<=|>|<|==)\s*(\d+\.\d+(?:\.\d+)?)$")


def _tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def accepts(spec: str, version: str | None = None) -> bool:
    """True when `version` (default: this install) satisfies one spec.

    A comma joins bounds: `iris>=0.4,<1.0`.
    """
    parts = [part.strip() for part in spec.split(",") if part.strip()]
    if len(parts) > 1:
        return all(accepts(part if part.startswith("iris") else f"iris{part}", version) for part in parts)
    current = _tuple(version or __version__)
    text = parts[0] if parts else spec.strip()
    if text in {"iris", "*"}:
        return True
    match = _SPEC.match(text)
    if match is None:
        raise ValueError(f"unsupported requirement {spec!r}")
    op, wanted = match.group(1), _tuple(match.group(2))
    if op == ">=":
        return current >= wanted
    if op == "<=":
        return current <= wanted
    if op == ">":
        return current > wanted
    if op == "<":
        return current < wanted
    return current == wanted


def accepts_all(specs: list[str], version: str | None = None) -> bool:
    return all(accepts(spec, version) for spec in specs)
