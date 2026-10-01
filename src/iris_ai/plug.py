"""One loader for every swappable part of Iris.

A component is a folder, a built-in name, or `package.module:Class`. Local
folders live in `components/<kind>/<name>/` and are imported by file path, so
the current directory does not matter. A component that fails to import does
not stop boot: the previous choice, or the built-in, is used instead.

Iris never edits its own package. Self-written components land in
`components/.staging` first, and move into place only after a check and an
explicit approval.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("iris.plug")

KINDS = (
    "context",
    "memory",
    "persona",
    "capture",
    "consolidator",
    "channel",
    "tools",
    "guard",
    "hook",
    "model",
    "judge",
    "secret_store",
)

_FAIL_LIMIT = 3


@dataclass
class ComponentContext:
    """The narrow API a component is allowed to use.

    `runtime` is available for built-ins that predate this context. New
    components should use `llm`, `memory`, `files`, `log` and `options`.
    """

    runtime: Any
    options: dict[str, Any] = field(default_factory=dict)

    @property
    def llm(self) -> Any:
        return getattr(self.runtime, "llm", None)

    @property
    def memory(self) -> Any:
        return getattr(self.runtime, "index", None)

    @property
    def files(self) -> Any:
        return getattr(self.runtime, "files", None)


@dataclass(frozen=True)
class ComponentInfo:
    kind: str
    name: str
    source: str
    description: str = ""
    path: Path | None = None


def components_root(root: Path | None = None) -> Path:
    return Path(root) if root is not None else Path("components")


def lock_path() -> Path:
    from iris_ai.config import settings

    return Path(settings.harness_config).parent / "components.lock"


def local_folder(kind: str, name: str, *, root: Path | None = None) -> Path | None:
    folder = components_root(root) / kind / name
    if (folder / "component.py").is_file():
        return folder
    return None


def list_local(kind: str | None = None, *, root: Path | None = None) -> list[ComponentInfo]:
    base = components_root(root)
    if not base.is_dir():
        return []
    found: list[ComponentInfo] = []
    kinds = [kind] if kind else [item.name for item in base.iterdir() if item.is_dir()]
    for item in kinds:
        if item.startswith("."):
            continue
        parent = base / item
        if not parent.is_dir():
            continue
        for folder in sorted(parent.iterdir()):
            if not folder.is_dir() or folder.name.startswith("."):
                continue
            if not (folder / "component.py").is_file():
                continue
            meta = _read_toml(folder / "component.toml")
            found.append(
                ComponentInfo(
                    kind=str(meta.get("kind") or item),
                    name=str(meta.get("name") or folder.name),
                    source="local",
                    description=str(meta.get("description") or ""),
                    path=folder,
                )
            )
    return found


def staging_dir(kind: str, name: str) -> Path:
    """A folder under `components/.staging` and nowhere else."""
    if any(part in {kind, name} for part in ("", ".", "..")) or any(
        sep in f"{kind}{name}" for sep in ("/", "\\", "..")
    ):
        raise ValueError("component path escapes the staging directory")
    base = (components_root() / ".staging").resolve()
    dest = (base / kind / name).resolve()
    if not dest.is_relative_to(base):
        raise ValueError("component path escapes the staging directory")
    return dest


def load_class(folder: Path) -> type:
    """Import `component.py` from a folder. The folder need not be on sys.path."""
    meta = _read_toml(folder / "component.toml")
    entry = str(meta.get("entry") or "component:Component")
    _module, _, attr = entry.partition(":")
    attr = attr or "Component"
    path = folder / "component.py"
    module_name = f"iris_component_{folder.parent.name}_{folder.name}".replace("-", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"{path} has no attribute {attr}") from exc


def construct(cls: type, runtime: Any, options: dict | None = None) -> Any:
    """Build a component. Classes that ask for `ctx` get a ComponentContext."""
    import inspect

    ctx = ComponentContext(runtime, options or {})
    try:
        params = list(inspect.signature(cls).parameters)
    except (TypeError, ValueError):
        params = []
    if params and params[0] in {"ctx", "context"}:
        return cls(ctx, **(options or {}))
    try:
        return cls(runtime)
    except TypeError:
        return cls()


def check_folder(folder: Path) -> tuple[bool, str]:
    """Import the component and confirm it has the method its kind requires."""
    meta = _read_toml(folder / "component.toml")
    kind = str(meta.get("kind") or folder.parent.name)
    required = {
        "context": "assemble_turn",
        "memory": "search",
        "persona": "text",
        "capture": "maybe_capture",
        "consolidator": "sleep",
        "channel": "send_message",
    }.get(kind)
    try:
        cls = load_class(folder)
    except Exception as exc:  # noqa: BLE001 - the check reports the import error
        return False, f"import failed: {type(exc).__name__}: {exc}"
    if required and not hasattr(cls, required):
        return False, f"{kind} component must define {required}()"
    return True, f"{kind}/{folder.name} matches the {required or kind} contract"


def scaffold(kind: str, name: str, *, root: Path | None = None) -> Path:
    """Write a working local component. Returns the folder."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    folder = components_root(root) / kind / name
    folder.mkdir(parents=True, exist_ok=True)
    class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_")) or "Component"
    method = {
        "context": "async def assemble_turn(self, user_message, *, session_id):\n        return user_message, []\n",
        "persona": "def text(self) -> str:\n        return 'A custom persona.'\n",
        "capture": "async def maybe_capture(self, *, user_message, reply, known_context):\n        return ''\n",
        "consolidator": "async def sleep(self):\n        return None\n",
        "memory": "async def connect(self):\n        return None\n\n    async def close(self):\n        return None\n\n    async def search(self, query, **kwargs):\n        return []\n",
    }.get(kind, "def ready(self) -> bool:\n        return True\n")
    (folder / "component.toml").write_text(
        f'kind = "{kind}"\nname = "{name}"\nentry = "component:{class_name}"\n'
        f'description = "Local {kind} component."\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        f'"""Local {kind} component `{name}`."""\n\n\nclass {class_name}:\n'
        f"    def __init__(self, ctx, **options):\n        self.ctx = ctx\n        self.options = options\n\n"
        f"    {method}",
        encoding="utf-8",
    )
    (folder / "test_component.py").write_text(
        f'"""Conformance smoke test for {name}."""\n\n'
        f"def test_imports():\n    from pathlib import Path\n"
        f"    from iris_ai.plug import check_folder\n\n"
        f"    ok, detail = check_folder(Path(__file__).parent)\n"
        f"    assert ok, detail\n",
        encoding="utf-8",
    )
    return folder


def eject(kind: str, name: str, *, dest_name: str = "") -> Path:
    """Copy a built-in into a local folder so it can be edited."""
    target = dest_name or f"{name}-local"
    folder = scaffold(kind, target)
    note = folder / "component.py"
    note.write_text(
        note.read_text(encoding="utf-8")
        + f"\n# Ejected from built-in {kind!r} {name!r}. Edit this file, then\n"
        + f"# `iris components use {kind} {target}` and /reload.\n",
        encoding="utf-8",
    )
    return folder


def activate(kind: str, name: str) -> str:
    """Move a staged component into place and select it."""
    staged = staging_dir(kind, name)
    if not (staged / "component.py").is_file():
        raise FileNotFoundError(f"nothing staged at {staged}")
    ok, detail = check_folder(staged)
    if not ok:
        raise RuntimeError(detail)
    dest = components_root() / kind / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(dest))
    _select(kind, name)
    _remember(kind, name)
    return f"activated {kind}/{name}"


def rollback(kind: str) -> str:
    """Select the previous component for this kind."""
    data = _read_lock()
    entry = data.get(kind) or {}
    previous = str(entry.get("previous") or "")
    if not previous:
        previous = _builtin_default(kind)
    _select(kind, previous)
    entry["active"] = previous
    entry["fails"] = 0
    data[kind] = entry
    _write_lock(data)
    return f"{kind} rolled back to {previous}"


def note_failure(kind: str, name: str) -> str | None:
    """Count a runtime failure. Roll back after the limit. Returns a message."""
    data = _read_lock()
    entry = data.get(kind) or {"active": name, "previous": _builtin_default(kind), "fails": 0}
    entry["fails"] = int(entry.get("fails") or 0) + 1
    data[kind] = entry
    _write_lock(data)
    if entry["fails"] < _FAIL_LIMIT:
        return None
    return rollback(kind)


class Guarded:
    """Call the component. After repeated failures, use the fallback."""

    def __init__(self, inner: Any, fallback: Any, *, kind: str, name: str) -> None:
        self._inner = inner
        self._fallback = fallback
        self.kind = kind
        self.name = name

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def assemble_turn(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("assemble_turn", *args, **kwargs)

    async def maybe_capture(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("maybe_capture", *args, **kwargs)

    async def sleep(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("sleep", *args, **kwargs)

    def text(self) -> str:
        try:
            return str(self._inner.text())
        except Exception as exc:  # noqa: BLE001 - a persona must not take down the turn
            log.warning("persona %s failed: %s", self.name, exc)
            note_failure(self.kind, self.name)
            if self._fallback is not None:
                return str(self._fallback.text())
            return ""

    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        target = self._inner
        try:
            return await getattr(target, method)(*args, **kwargs)
        except Exception as exc:
            log.warning("%s %s failed: %s", self.kind, self.name, exc)
            switched = note_failure(self.kind, self.name)
            if switched:
                log.warning(switched)
            if self._fallback is not None and hasattr(self._fallback, method):
                return await getattr(self._fallback, method)(*args, **kwargs)
            raise


def _builtin_default(kind: str) -> str:
    return {
        "context": "default",
        "memory": "sqlite",
        "persona": "file",
        "capture": "default",
        "consolidator": "dreaming",
        "channel": "terminal",
    }.get(kind, "default")


def _select(kind: str, name: str) -> None:
    from iris_ai.cli.toml_edit import upsert
    from iris_ai.config import settings

    path = Path(settings.harness_config)
    if kind == "memory":
        upsert(path, "memory_backend", name)
    elif kind == "channel":
        upsert(path, "enabled", name, table="channels")
    else:
        upsert(path, kind, name, table="components")


def _remember(kind: str, name: str) -> None:
    data = _read_lock()
    current = str((data.get(kind) or {}).get("active") or _builtin_default(kind))
    data[kind] = {"active": name, "previous": current, "fails": 0}
    _write_lock(data)


def _read_lock() -> dict:
    path = lock_path()
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_lock(data: dict) -> None:
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    import tomllib

    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
