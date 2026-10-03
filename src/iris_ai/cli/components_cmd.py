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
        known = set(list_options(kind))
        if option not in known and ":" not in option:
            ui.failed(out, "unknown option", f"{option!r}. known: {', '.join(sorted(known))}")
            return 2
        path = Path(settings.harness_config)
        if kind == "memory":
            upsert(path, "memory_backend", option)
            settings.memory_backend = option
        elif kind == "channel":
            upsert(path, "enabled", [option], table="channels")
        else:
            upsert(path, kind, option, table="components")
        from iris_ai.plug import _remember

        _remember(kind, option)
        out.print(f"[iris.ok]using[/iris.ok] {kind} = {option}")
        out.print("apply it with /reload in chat, or start iris again")
        return 0
    if action == "eject":
        from iris_ai.plug import eject

        folder = eject(kind, option or "default")
        out.print(f"wrote {folder}")
        return 0
    if action == "check":
        from iris_ai.plug import check_in_sandbox, local_folder

        folder = local_folder(kind, option) if kind else Path(option)
        if folder is None:
            ui.failed(out, "not found", option or kind)
            return 2
        ok, detail = check_in_sandbox(folder)
        ui.status(out, "ok" if ok else "fail", "component", detail)
        return 0 if ok else 1
    if action == "rollback":
        from iris_ai.plug import rollback

        out.print(rollback(kind))
        return 0
    if action == "add":
        source = Path(option or kind)
        if not source.is_dir():
            ui.failed(out, "not a directory", str(source))
            return 2
        import shutil

        from iris_ai.plug import components_root

        meta_kind = kind or source.parent.name
        dest = components_root() / meta_kind / source.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source, dest)
        out.print(f"installed {dest}")
        return 0
    ui.failed(
        out,
        "usage:",
        "iris components | use | eject | check | rollback | add",
    )
    return 2
