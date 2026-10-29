"""List the plug-and-play options and switch one."""

from __future__ import annotations

from pathlib import Path

from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.cli.toml_edit import upsert
from iris_ai.components import OPTIONS, list_options
from iris_ai.config import settings


def _lock(out, mode: str) -> int:
    """`iris components lock check` exits 1 when a pin and the folder disagree."""
    from iris_ai.components.lock import read_lock
    from iris_ai.plug import component_digest, local_folder

    if mode in {"update", "--update"}:
        changed = 0
        for kind_name, row in (read_lock().get("kinds") or {}).items():
            name = str(row.get("active") or "")
            folder = local_folder(kind_name, name) if name else None
            if folder is None:
                continue
            if not _guarded_select(out, kind_name, name):
                return 1
            changed += 1
        out.print(f"re-pinned {changed}")
        return 0
    if mode not in {"check", "--check", ""}:
        ui.failed(out, "usage:", "iris components lock check")
        return 2
    drifted: list[str] = []
    for kind, row in (read_lock().get("kinds") or {}).items():
        name = str(row.get("active") or "")
        folder = local_folder(kind, name) if name else None
        pinned = str(row.get("digest") or "")
        if folder is not None and pinned and component_digest(folder) != pinned:
            drifted.append(f"{kind} {name}")
    if drifted:
        out.print("drift " + ", ".join(drifted))
        return 1
    out.print("lock matches")
    return 0


def _inspect(out, kind: str, name: str) -> int:
    """Manifest, source, digests, and the lock row. The trust picture."""
    import tomllib

    from iris_ai.components.lock import read_lock
    from iris_ai.plug import component_digest, local_folder

    if not kind or not name:
        ui.failed(out, "usage:", "iris components inspect <kind> <name>")
        return 2
    folder = local_folder(kind, name)
    meta: dict = {}
    if folder is not None and (folder / "component.toml").is_file():
        with (folder / "component.toml").open("rb") as handle:
            loaded = tomllib.load(handle)
        meta = loaded if isinstance(loaded, dict) else {}
    current = component_digest(folder) if folder is not None else ""
    row = (read_lock().get("kinds") or {}).get(kind) or {}
    source = "local" if folder is not None else ("dotted" if ":" in name else "builtin")
    out.print(f"{kind} {name}")
    out.print(f"source {source}")
    if folder is not None:
        out.print(f"path {folder}")
    out.print(f"api_version {meta.get('api_version') or row.get('api_version') or 'iris/v0'}")
    out.print(f"permissions {', '.join(meta.get('permissions') or row.get('permissions') or []) or '(none)'}")
    out.print(f"pinned {(row.get('digest') or '')[:12] or '(not pinned)'}")
    out.print(f"current {current[:12] or '(built-in)'}")
    if row.get("suite"):
        out.print(f"suite {row.get('suite')} at {row.get('verified_at') or '(unknown)'}")
    else:
        out.print("suite (not verified)")
    if row.get("active"):
        out.print(f"lock active={row.get('active')} previous={row.get('previous') or '(none)'} fails={row.get('fails', 0)}")
    return 0


def _remote_install(source: str) -> bool:
    """Git and URL installs are refused. A local folder is the only door."""
    text = source.strip().lower()
    return text.startswith(("http://", "https://", "git@", "git+", "file:", "ssh://"))


def _manifest_identity(folder: Path) -> tuple[str, str] | None:
    """`(kind, name)` from component.toml. None when the file does not name both."""
    import tomllib

    meta_path = folder / "component.toml"
    if not meta_path.is_file():
        return None
    with meta_path.open("rb") as handle:
        loaded = tomllib.load(handle)
    if not isinstance(loaded, dict):
        return None
    kind = str(loaded.get("kind") or "")
    name = str(loaded.get("name") or "")
    if not kind or not name:
        return None
    return kind, name


def _active(kind: str) -> str:
    if kind == "memory":
        return settings.memory_backend
    if kind == "channel":
        return settings.channels_enabled or "terminal"
    return kind


def _verify(out, kind: str, name: str, suite: str = "") -> int:
    """Run the behaviour suite. Non-zero when the component fails it."""
    import asyncio

    from iris_ai.plug import construct, load_class, local_folder
    from iris_ai.testing import run_conformance, suite_name

    if not kind or not name:
        ui.failed(out, "usage:", "iris components verify <kind> <name> [--suite SUITE]")
        return 2
    folder = local_folder(kind, name)
    if folder is None:
        ui.failed(out, "not found", f"{kind}/{name}")
        return 2
    try:
        component = construct(load_class(folder), None, trust="local")
    except Exception as exc:  # noqa: BLE001 - verify reports the load error
        ui.failed(out, "load failed", f"{type(exc).__name__}: {exc}")
        return 1
    problems = asyncio.run(run_conformance(kind, component, suite=suite))
    if problems:
        for item in problems:
            out.print(item)
        return 1
    out.print(f"passed {suite or suite_name(kind)}")
    return 0


def _guarded_select(out, kind: str, option: str) -> bool:
    """Pin through the shared door. A failed folder is not selected."""
    from iris_ai.lifecycle.control import select

    try:
        select(kind, option)
    except RuntimeError as exc:
        ui.failed(out, "verification failed", str(exc))
        return False
    return True


def run(
    action: str = "list",
    kind: str = "",
    option: str = "",
    *,
    live: bool = False,
    staged: bool = False,
    yes: bool = False,
    as_plugin: str = "",
    suite: str = "",
    dry_run: bool = False,
) -> int:
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
        parts = [part.strip() for part in option.split(",") if part.strip()]
        if len(parts) > 1:
            unknown = [part for part in parts if part not in known and ":" not in part]
            if unknown:
                ui.failed(out, "unknown option", f"{', '.join(unknown)}. known: {', '.join(sorted(known))}")
                return 2
        elif option not in known and ":" not in option:
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
        if not _guarded_select(out, kind, option):
            return 1
        out.print(f"[iris.ok]using[/iris.ok] {kind} = {option}")
        out.print("apply it with /reload in chat, or start iris again")
        return 0
    if action == "eject":
        from iris_ai.plug import eject

        folder = eject(kind, option or "default")
        out.print(f"wrote {folder}")
        return 0
    if action == "verify":
        return _verify(out, kind, option, suite)
    if action == "check":
        from iris_ai.plug import check_in_sandbox, local_folder

        folder = local_folder(kind, option) if kind else Path(option)
        if folder is None:
            ui.failed(out, "not found", option or kind)
            return 2
        ok, detail = check_in_sandbox(folder)
        ui.status(out, "ok" if ok else "fail", "component", detail)
        return 0 if ok else 1
    if action == "lock":
        return _lock(out, kind)
    if action == "simulate":
        from iris_ai.eval.score import compare_suite, list_suites, refuse_live

        try:
            refuse_live(live, approved=not staged)
        except PermissionError as exc:
            out.print(str(exc))
            return 1
        if staged:
            from iris_ai.plug import local_folder, simulate_staged, staging_dir

            staged_name = option or "default"
            try:
                folder = staging_dir(kind, staged_name)
            except ValueError:
                folder = None
            if folder is None or not (folder / "component.py").is_file():
                folder = local_folder(kind, staged_name)
            if folder is None:
                out.print(f"no staged {kind}/{staged_name}")
                return 1
            import tempfile

            world = Path(tempfile.mkdtemp())
            (world / "marker.txt").write_text("fixture", encoding="utf-8")
            ok, detail = simulate_staged(folder, world, live=False)
            out.print(detail)
            return 0 if ok else 1
        paths = list_suites(kind)
        if not paths:
            out.print(f"no suites for {kind}")
            return 1
        name = option or "default"
        for path in paths:
            row = compare_suite(path, name, against="default")
            low, high = row["ci"]
            out.print(
                f"{row['suite']}  {name} vs default  {row['metric']} "
                f"{row['baseline']:.2f} -> {row['candidate']:.2f}  "
                f"CI [{low:+.2f}, {high:+.2f}]  {row['verdict']}"
            )
        return 0
    if action == "inspect":
        return _inspect(out, kind, option)
    if action == "rollback":
        from iris_ai.lifecycle.control import rollback

        out.print(rollback(kind))
        return 0
    if action == "add":
        source_text = option or kind
        if _remote_install(source_text):
            out.print(
                "git and http installs are refused. "
                "Copy the folder locally, then `iris components add <path> --yes`."
            )
            return 2
        source = Path(source_text)
        if not source.is_dir():
            ui.failed(out, "not a directory", str(source))
            return 2
        identity = _manifest_identity(source)
        if identity is None:
            ui.failed(out, "not a component", "component.toml must set kind and name")
            return 2
        from iris_ai.plug import FOLDER_KINDS, check_in_sandbox, component_digest, components_root

        meta_kind, meta_name = identity
        if meta_kind not in FOLDER_KINDS:
            ui.failed(out, "unknown kind", meta_kind)
            return 2
        digest = component_digest(source)
        out.print(f"digest {digest[:12]}. The folder is checked, then the approved bytes are stored and pinned.")
        if not yes:
            out.print("re-run with --yes to check, store, and pin it")
            return 2
        ok, detail = check_in_sandbox(source)
        if not ok:
            ui.failed(out, "check failed", detail)
            return 1
        dest = components_root() / meta_kind / meta_name
        if dest.resolve() != source.resolve():
            import shutil

            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(source, dest, ignore=shutil.ignore_patterns(".git"))
        if not _guarded_select(out, meta_kind, meta_name):
            return 1
        out.print(f"installed {dest}")
        return 0
    if action == "export":
        from iris_ai.plug import local_folder
        from iris_ai.plugins_interop import export_component

        if not as_plugin or not option:
            out.print("usage: iris components export <kind> <name> --as-plugin DIR")
            return 2
        catalog = Path(__file__).resolve().parents[1] / "catalog" / kind / option / "component.py"
        folder = local_folder(kind, option)
        source = catalog if catalog.is_file() else (folder / "component.py" if folder else Path(option))
        if not source.is_file():
            ui.failed(out, "not found", f"{kind}/{option}")
            return 2
        dest = export_component(kind, option, source, Path(as_plugin))
        out.print(f"wrote {dest / 'plugin.json'}")
        return 0
    ui.failed(
        out,
        "usage:",
        "iris components | use | eject | check | verify | rollback | add | inspect | export | matrix",
    )
    return 2
