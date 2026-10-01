"""List the plug-and-play options and switch one."""

from __future__ import annotations

from pathlib import Path

from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.cli.toml_edit import upsert
from iris_ai.components import OPTIONS, list_options
from iris_ai.config import settings


def _active(kind: str) -> str:
    if kind == "memory":
        return settings.memory_backend
    if kind == "channel":
        return settings.channels_enabled or "terminal"
    return kind


def run(action: str = "list", kind: str = "", option: str = "") -> int:
    out = console()
    if action in {"list", "ls", ""} and not kind:
        for name in OPTIONS:
            choices = ", ".join(list_options(name))
            out.print(f"[iris.cmd]{name}[/iris.cmd]  {choices}")
        ui.note(out, "switch with `iris components use <kind> <option>`")
        ui.note(out, "or scaffold one with `iris new <kind> <name>`")
        return 0
    if action == "use":
        if kind not in OPTIONS:
            ui.failed(out, "unknown kind", f"{kind!r}. known: {', '.join(OPTIONS)}")
            return 2
        if option not in OPTIONS[kind] and ":" not in option:
            ui.failed(out, "unknown option", f"{option!r}. known: {', '.join(OPTIONS[kind])}")
            return 2
        path = Path(settings.harness_config)
        if kind == "memory":
            upsert(path, "memory_backend", option)
            settings.memory_backend = option
        elif kind == "channel":
            upsert(path, "enabled", [option], table="channels")
        else:
            upsert(path, kind, option, table="components")
        out.print(f"[iris.ok]using[/iris.ok] {kind} = {option}")
        return 0
    ui.failed(out, "usage:", "iris components | iris components use <kind> <option>")
    return 2
