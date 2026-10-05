"""One resolver: built-in, local folder, entry point, dotted path."""

from __future__ import annotations

from typing import Any


def resolve(kind: str, name: str) -> tuple[Any, str] | None:
    """Return `(object, source)` or None when the name is a mode like `off`.

    Order: reserved built-in, local folder, `iris_ai.components` entry point,
    then a dotted path. The first that exists wins.
    """
    import importlib.metadata as metadata

    from iris_ai.artifacts.store import executable_folder
    from iris_ai.components import _builtin, load_symbol
    from iris_ai.plug import load_class

    builtin = _builtin(kind, name)
    if name in _reserved(kind):
        if builtin is None and name in {"default", "off", "dreaming", "blank", "file"}:
            return None
        if builtin is not None:
            return builtin, "builtin"
        return None
    folder = executable_folder(kind, name)
    if folder is not None:
        return load_class(folder), "local"
    try:
        points = metadata.entry_points(group="iris_ai.components")
    except TypeError:  # pragma: no cover - older importlib
        points = [item for item in metadata.entry_points() if item.group == "iris_ai.components"]
    for point in points:
        if point.name == f"{kind}.{name}":
            return point.load(), "entry-point"
    if ":" in name:
        return load_symbol(name), "dotted"
    if builtin is not None:
        return builtin, "builtin"
    return None


def _reserved(kind: str) -> set[str]:
    from iris_ai.components import OPTIONS

    return set(OPTIONS.get(kind, ()))
