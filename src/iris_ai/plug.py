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

import hashlib
import importlib.util
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from iris_ai.sdk.context import ComponentContext

log = logging.getLogger("iris.plug")

# Folder components a person or Iris can write. engine joins with the engine
# seam; channel joins with the channel runner. Everything else is an entry point.
FOLDER_KINDS = (
    "context",
    "memory",
    "persona",
    "capture",
    "consolidator",
)

KINDS = (
    *FOLDER_KINDS,
    "channel",
    "tools",
    "guard",
    "hook",
    "model",
    "judge",
    "secret_store",
)

_ENTRY_POINT_HINTS = {
    "channel": "Register a factory under the entry point group iris_ai.channels, then set [channels] enabled.",
    "tools": "Register an object with .name and .tools(runtime) under iris_ai.tools.",
    "hook": "Register a callable attach(bus) under iris_ai.hooks.",
    "guard": "Register a callable attach(bus) under iris_ai.hooks. A guard subscribes to pre_tool.",
    "model": "Register a factory returning a ModelBackend under iris_ai.models. Select it with MODEL_BACKEND.",
    "judge": "Register a factory returning a Judge under iris_ai.judges. Select it with JUDGE_BACKEND.",
    "secret_store": "Register a backend under iris_ai.secret_stores. Select it with SECRET_STORE.",
}


def entry_point_hint(kind: str) -> str:
    """What to do instead of scaffolding a folder nothing loads."""
    detail = _ENTRY_POINT_HINTS.get(kind, "This kind is not a folder next to config/.")
    return (
        f"{kind} is not a folder component. {detail} "
        "See DOCS.md section 10.2. "
        "Folder components are context, memory, persona, capture, and consolidator."
    )

_FAIL_LIMIT = 3


@dataclass(frozen=True)
class ComponentInfo:
    kind: str
    name: str
    source: str
    description: str = ""
    path: Path | None = None


def components_root(root: Path | None = None) -> Path:
    """The components directory.

    An explicit `root` is that directory (callers that scaffold into a chosen
    folder pass it). Otherwise components live next to the `config/` directory
    that holds the harness file, found from the working directory or a parent.
    """
    if root is not None:
        return Path(root)
    return _project_root() / "components"


def _project_root() -> Path:
    """Directory that contains `config/` for the harness file, or the cwd."""
    from iris_ai.config import settings

    config = Path(settings.harness_config)
    if config.is_absolute():
        return _root_from_config(config)
    here = Path.cwd()
    for candidate in (here, *here.parents):
        found = candidate / config
        if found.is_file():
            return _root_from_config(found)
    return here


def _root_from_config(config: Path) -> Path:
    if config.parent.name == "config":
        return config.parent.parent
    return config.parent


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
    module_name = f"iris_component_{folder.parent.name}_{folder.name}".replace("-", "_").replace(".", "_")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Bytecode must not land inside the component folder. The digest is the
    # artifact identity, and a cache file would change it after the first import.
    import tempfile

    previous_prefix = sys.pycache_prefix
    previous_bytecode = sys.dont_write_bytecode
    sys.pycache_prefix = str(Path(tempfile.gettempdir()) / "iris-pyc")
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.pycache_prefix = previous_prefix
        sys.dont_write_bytecode = previous_bytecode
    try:
        component = getattr(module, attr)
    except AttributeError as exc:
        raise ImportError(f"{path} has no attribute {attr}") from exc
    component.api_version = str(meta.get("api_version") or "")
    component.component_name = str(meta.get("name") or folder.name)
    component.component_kind = str(meta.get("kind") or folder.parent.name)
    component.permissions = tuple(meta.get("permissions") or ())
    return component


def _v1_context(cls: type, runtime: Any, options: dict | None) -> ComponentContext:
    """Capabilities named in the manifest. The runtime itself is not one of them."""
    from iris_ai.sdk.context import MemoryAccess, ModelAccess, StateDir, WorkspaceAccess

    granted = set(getattr(cls, "permissions", ()) or ())
    root = getattr(getattr(runtime, "files", None), "root", None)
    kind = getattr(cls, "component_kind", "")
    name = getattr(cls, "component_name", "")
    return ComponentContext(
        api_version="iris/v1",
        kind=kind,
        name=name,
        options=options or {},
        llm=ModelAccess(getattr(runtime, "llm", None)) if "llm" in granted else None,
        memory=MemoryAccess(getattr(runtime, "index", None), getattr(runtime, "files", None))
        if "memory.read" in granted
        else None,
        files=WorkspaceAccess(root) if "files.read" in granted and root is not None else None,
        state=StateDir(Path(root) / "state" / kind / name) if "state" in granted and root is not None else None,
    )


def construct(
    cls: type,
    runtime: Any,
    options: dict | None = None,
    *,
    trust: str = "untrusted",
) -> Any:
    """Build a component. Classes that ask for `ctx` get a v1 ComponentContext.

    Only `trust="builtin"` may receive the runtime object. A local, installed,
    or dotted component must take `ctx`, or take no required arguments. Passing
    the runtime and retrying on TypeError used to hide a real constructor error
    and hand the kernel to any class that asked for it.
    """
    import inspect

    try:
        params = list(inspect.signature(cls).parameters.values())
    except (TypeError, ValueError):
        params = []
    names = [param.name for param in params]
    if names and names[0] in {"ctx", "context"}:
        return cls(_v1_context(cls, runtime, options), **(options or {}))
    if trust != "builtin":
        required = [
            param
            for param in params
            if param.default is inspect.Parameter.empty
            and param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD)
        ]
        if required:
            raise TypeError(
                f"{getattr(cls, '__name__', cls)} must take ctx as its first parameter. "
                "Local components do not receive the runtime."
            )
        return cls()
    if names:
        return cls(runtime)
    return cls()


_DIGEST_SKIP_DIRS = {"__pycache__", ".git", ".mypy_cache", ".ruff_cache", ".pytest_cache"}
_DIGEST_SKIP_SUFFIXES = {".pyc", ".pyo"}
_DIGEST_TEXT_SUFFIXES = {".py", ".toml", ".md", ".txt", ".json", ".yaml", ".yml"}


def activation_report(folder: Path) -> dict[str, Any]:
    """What the approval card shows. Generated from the staged bytes, not the model."""
    meta = _read_toml(folder / "component.toml")
    source = folder / "component.py"
    preview = ""
    if source.is_file():
        preview = "\n".join(source.read_text(encoding="utf-8").splitlines()[:40])
    from iris_ai.config import settings

    kind = str(meta.get("kind") or "")
    if settings.component_host == "subprocess" and kind in {"context", "capture", "consolidator"}:
        execution = (
            "subprocess with a scrubbed environment and no Iris kernel on its path. "
            "This is an audit boundary, not a kernel jail."
        )
    else:
        execution = "in-process with your privileges until an isolated host is enabled"
    return {
        "permissions": [str(item) for item in (meta.get("permissions") or [])],
        "api_version": str(meta.get("api_version") or ""),
        "preview": preview,
        "execution": execution,
    }


def canonical_files(folder: Path) -> list[tuple[str, bytes]]:
    """The files that make up a component's identity, in digest order.

    Paths are posix, text is LF, and runtime artifacts are left out. The store
    writes these bytes so a stored copy hashes the same as the folder it came from.
    """
    if not folder.is_dir():
        return []
    found: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        if any(part in _DIGEST_SKIP_DIRS for part in relative.parts):
            continue
        if path.suffix in _DIGEST_SKIP_SUFFIXES or path.name.startswith("."):
            continue
        found.append(path)
    rows: list[tuple[str, bytes]] = []
    for path in sorted(found, key=lambda item: item.relative_to(folder).as_posix()):
        payload = path.read_bytes()
        if path.suffix in _DIGEST_TEXT_SUFFIXES:
            payload = payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        rows.append((path.relative_to(folder).as_posix(), payload))
    return rows


def component_digest(folder: Path) -> str:
    """Stable sha256 of the component source.

    Runtime artifacts (`__pycache__`, bytecode) are not part of the identity.
    Text files are hashed with LF endings so the digest does not depend on the
    editor that wrote them.
    """
    digest = hashlib.sha256()
    for relative, payload in canonical_files(folder):
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def rollback_target(kind: str) -> tuple[str, str]:
    """`(active, previous)` for a kind. Previous falls back to the built-in."""
    from iris_ai.components.lock import read_lock

    entry = (read_lock().get("kinds") or {}).get(kind) or {}
    active = str(entry.get("active") or "")
    previous = str(entry.get("previous") or "") or _builtin_default(kind)
    if previous == active:
        previous = _builtin_default(kind)
    return active, previous


# What a component check may inherit. API keys, tokens and the rest of the
# parent environment are not in this list, so staged code cannot read them.
_CHECK_ENV = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
    "PYTHONDONTWRITEBYTECODE",
)

# Interpreter locations a check may read so `import` still works. The project
# directory is not in this list: an editable install puts the repo on
# `sys.path`, and allowing it would let staged code open `.env`.
def _interpreter_read_roots() -> list[str]:
    import iris_ai

    roots: list[Path] = []
    if sys.prefix:
        roots.append(Path(sys.prefix).resolve())
    roots.append(Path(os.__file__).resolve().parent)
    roots.append(Path(iris_ai.__file__).resolve().parent)
    unique: list[str] = []
    for root in roots:
        text = str(root)
        if text not in unique:
            unique.append(text)
    return unique


_CHECK_CHILD = r"""
import asyncio, json, os, runpy, sys
from pathlib import Path

# The selector loop opens a self-pipe. Create it before Landlock and seccomp,
# or the contract probe dies with EPERM on socketpair (context, capture,
# consolidator). Persona probes are synchronous and never hit this.
asyncio.set_event_loop(asyncio.new_event_loop())

sandbox = Path(sys.argv[1]).resolve()
read_roots = [sandbox]
for item in json.loads(sys.argv[2]):
    read_roots.append(Path(item).resolve())
os.chdir(sandbox)

from iris_ai.isolation.linux_jail import apply as _kernel_isolation
isolation = _kernel_isolation(sandbox, read_roots)

def _inside(path, roots) -> bool:
    try:
        resolved = Path(os.fsdecode(path)).resolve()
    except (OSError, ValueError, TypeError):
        return False
    return any(resolved == root or root in resolved.parents for root in roots)

_SPAWN = {"os.system", "os.fork", "os.forkpty", "os.posix_spawn", "os.spawn", "subprocess.Popen"}
_NET = {
    "socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
    "socket.gethostbyname", "socket.gethostbyname_ex", "socket.gethostbyaddr", "socket.getnameinfo",
}
_MUTATE = {
    "os.remove", "os.unlink", "os.rmdir", "os.rename", "os.replace", "os.truncate", "os.utime",
    "os.mkdir", "os.makedirs", "os.chmod", "os.chown", "os.lchown", "os.link",
    "os.symlink", "os.chflags", "shutil.rmtree",
}
_NATIVE = {"ctypes", "_ctypes", "cffi", "_cffi_backend"}

def _audit(event, args):
    if event == "import" and args:
        name = str(args[0] or "")
        root = name.split(".", 1)[0]
        if name in _NATIVE or root in _NATIVE:
            raise PermissionError(f"native loader blocked in component sandbox: {name}")
    if event in _SPAWN or event.startswith(("os.exec", "os.spawn", "os.posix_spawn")):
        raise PermissionError(f"process spawn blocked in component sandbox: {event}")
    if event in _NET:
        raise PermissionError(f"network blocked in component sandbox: {event}")
    if event == "open" and args:
        path = args[0]
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        writing = isinstance(mode, str) and any(flag in mode for flag in "wax+")
        if isinstance(flags, int):
            writing = writing or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT))
        if writing:
            if not _inside(path, [sandbox]):
                raise PermissionError(f"write outside component sandbox: {path}")
        elif not _inside(path, read_roots):
            raise PermissionError(f"read outside component sandbox: {path}")
        return
    if event in _MUTATE:
        for arg in args:
            if isinstance(arg, (str, bytes, os.PathLike)) and not _inside(arg, [sandbox]):
                raise PermissionError(f"write outside component sandbox: {arg}")

sys.addaudithook(_audit)
from iris_ai.plug import check_folder
ok, detail = check_folder(sandbox)
detail = f"{detail}; isolation={isolation}"
if not ok:
    print(json.dumps({"ok": False, "detail": detail}))
    raise SystemExit(0)
test = sandbox / "test_component.py"
if test.is_file():
    try:
        runpy.run_path(str(test), run_name="__main__")
    except SystemExit as exc:
        if exc.code not in (0, None):
            print(json.dumps({"ok": False, "detail": f"test_component.py exited {exc.code}; isolation={isolation}"}))
            raise SystemExit(0)
    except Exception as exc:
        message = f"test_component.py failed: {type(exc).__name__}: {exc}; isolation={isolation}"
        print(json.dumps({"ok": False, "detail": message}))
        raise SystemExit(0)
    detail = f"{detail}; test_component.py passed"
world = os.environ.get("IRIS_SIMULATE_WORLD", "")
if world:
    marker = Path(world) / "marker.txt"
    seen = marker.read_text(encoding="utf-8").strip()
    detail = f"{detail}; world={seen}"
print(json.dumps({"ok": True, "detail": detail}))
"""


def _check_env(folder: Path) -> dict[str, str]:
    """A child environment with no secrets. Home and temp stay inside the folder."""
    env = {key: os.environ[key] for key in _CHECK_ENV if key in os.environ}
    root = str(folder.resolve())
    env["HOME"] = root
    env["USERPROFILE"] = root
    env["TMPDIR"] = root
    env["TMP"] = root
    env["TEMP"] = root
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


_UNSHARE_PREFIX: list[str] | None = None


def _sandbox_launcher() -> list[str]:
    """`unshare` arguments that put the check in a network namespace, when the kernel allows it.

    An empty list means the child has no network namespace. Landlock and seccomp
    still run inside the child on Linux. The result is cached: probing `unshare`
    on every check would fork twice.
    """
    global _UNSHARE_PREFIX
    if _UNSHARE_PREFIX is not None:
        return list(_UNSHARE_PREFIX)
    prefix: list[str] = []
    if sys.platform.startswith("linux"):
        unshare = shutil.which("unshare")
        if unshare:
            try:
                probe = subprocess.run(
                    [unshare, "--user", "--map-root-user", "--net", "true"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                probe = None
            if probe is not None and probe.returncode == 0:
                prefix = [unshare, "--user", "--map-root-user", "--net"]
    _UNSHARE_PREFIX = prefix
    return list(prefix)


def check_in_sandbox(folder: Path) -> tuple[bool, str]:
    """Contract-check a component, and run `test_component.py` when it exists.

    The child inherits no API keys. On Linux it applies Landlock (read under the
    component folder, the interpreter, and a few system dirs; write only inside
    the folder; execute denied) and a seccomp filter that rejects `execve` and
    new sockets. `unshare --net` is used when it works, so DNS has no route.
    An audit hook still turns Python-level spawn, DNS, `os.utime`, and imports
    of ctypes/cffi into `PermissionError`.

    That stops `ctypes` `system`, timestamp changes outside the folder, and
    `socket.gethostbyname`. It does not stop a kernel bug, and it does not
    contain the component after you approve it — approved code runs in-process.
    Where Landlock is missing, the import ban and the audit hook are the whole
    guarantee, and the check detail says `isolation=audit`.
    """
    if not (folder / "component.py").is_file():
        return False, f"no component.py in {folder}"
    env = _check_env(folder)
    launcher = _sandbox_launcher()
    if launcher:
        env["IRIS_CHECK_NETNS"] = "1"
    try:
        proc = subprocess.run(
            [
                *launcher,
                sys.executable,
                "-c",
                _CHECK_CHILD,
                str(folder.resolve()),
                json.dumps(_interpreter_read_roots()),
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
            cwd=str(folder.resolve()),
        )
    except subprocess.TimeoutExpired:
        return False, "component check timed out"
    line = ""
    for candidate in reversed(proc.stdout.splitlines()):
        if candidate.strip().startswith("{"):
            line = candidate.strip()
            break
    if not line:
        detail = (proc.stderr or proc.stdout or "check failed").strip()
        return False, detail[:300]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False, line[:300]
    return bool(payload.get("ok")), str(payload.get("detail") or "check failed")


def simulate_staged(folder: Path, world: Path, *, live: bool = False) -> tuple[bool, str]:
    """Score a staged folder in the check jail. The fixture world stays outside it.

    `--live` is refused. The component digest is compared before and after so a
    simulation cannot rewrite the files it is judging.
    """
    from iris_ai.eval.score import refuse_live

    refuse_live(live, approved=False)
    folder = Path(folder).resolve()
    world = Path(world).resolve()
    if world == folder or folder in world.parents or world in folder.parents:
        raise ValueError("fixture world must sit outside the component folder")
    if not (world / "marker.txt").is_file():
        raise ValueError("fixture world has no marker.txt")
    before = component_digest(folder)
    env = _check_env(folder)
    env["IRIS_SIMULATE_WORLD"] = str(world)
    launcher = _sandbox_launcher()
    if launcher:
        env["IRIS_CHECK_NETNS"] = "1"
    roots = _interpreter_read_roots()
    roots.append(str(world))
    try:
        proc = subprocess.run(
            [*launcher, sys.executable, "-c", _CHECK_CHILD, str(folder), json.dumps(roots)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            env=env,
            cwd=str(world),
        )
    except subprocess.TimeoutExpired:
        return False, "simulation timed out"
    after = component_digest(folder)
    if before != after:
        return False, "component digest changed during simulation"
    line = ""
    for candidate in reversed(proc.stdout.splitlines()):
        if candidate.strip().startswith("{"):
            line = candidate.strip()
            break
    if not line:
        detail = (proc.stderr or proc.stdout or "simulation failed").strip()
        return False, detail[:300]
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return False, line[:300]
    return bool(payload.get("ok")), str(payload.get("detail") or "simulation failed")


def _drive(coro: Any) -> Any:
    """Run a probe coroutine on the loop that already exists.

    `asyncio.run` builds a new loop, and on Linux that opens a socketpair.
    The component check applies a seccomp filter that rejects new sockets, so
    the probe has to reuse a loop created before the jail.
    """
    import asyncio

    try:
        current = asyncio.get_event_loop()
    except RuntimeError:
        current = None
    if current is None or current.is_closed():
        current = asyncio.new_event_loop()
        asyncio.set_event_loop(current)
    return current.run_until_complete(coro)


def _probe_v1(kind: str, cls: type) -> str:
    """Empty when the sample call returns the v1 type. Otherwise the reason."""
    from iris_ai.sdk.types import (
        CaptureRequest,
        ConsolidationPlan,
        ConsolidationRequest,
        ContextRequest,
        ContextResult,
        MemoryCandidate,
    )

    try:
        component = construct(cls, None)
        if kind == "context":
            result = _drive(component.assemble(ContextRequest(message="hi", session_id="probe")))
            if not isinstance(result, ContextResult):
                return "assemble() must return ContextResult"
        elif kind == "capture":
            result = _drive(
                component.extract(CaptureRequest(user_message="hi", reply="ok", session_id="probe"))
            )
            if not isinstance(result, list) or any(not isinstance(item, MemoryCandidate) for item in result):
                return "extract() must return a list of MemoryCandidate"
        elif kind == "consolidator":
            result = _drive(component.propose(ConsolidationRequest()))
            if not isinstance(result, ConsolidationPlan):
                return "propose() must return ConsolidationPlan"
        elif kind == "persona":
            rendered = component.text()
            if not isinstance(rendered, str):
                return "persona text() must return str"
    except Exception as exc:  # noqa: BLE001 - the probe reports the contract error
        return f"{kind} probe failed: {type(exc).__name__}: {exc}"
    return ""


def check_folder(folder: Path) -> tuple[bool, str]:
    """Import the component and confirm it has the method its kind requires."""
    meta = _read_toml(folder / "component.toml")
    kind = str(meta.get("kind") or folder.parent.name)
    v1 = str(meta.get("api_version") or "") == "iris/v1"
    required = {
        "context": "assemble" if v1 else "assemble_turn",
        "memory": "search",
        "persona": "text",
        "capture": "extract" if v1 else "maybe_capture",
        "consolidator": "propose" if v1 else "sleep",
        "channel": "send_message",
    }.get(kind)
    try:
        cls = load_class(folder)
    except Exception as exc:  # noqa: BLE001 - the check reports the import error
        return False, f"import failed: {type(exc).__name__}: {exc}"
    if v1:
        probed = _probe_v1(kind, cls)
        if probed:
            return False, probed
    if kind == "persona":
        # `iris doctor` calls `text()` with no arguments. The check has to do
        # the same, or a persona whose signature is `text(self, original)`
        # passes here and then fails the next boot.
        try:
            rendered = construct(cls, None).text()
        except Exception as exc:  # noqa: BLE001 - the check reports the contract error
            return False, f"persona text() failed: {type(exc).__name__}: {exc}"
        if not isinstance(rendered, str):
            return False, "persona text() must return str"
        return True, f"{kind}/{folder.name} matches the text contract"
    if required and not hasattr(cls, required):
        return False, f"{kind} component must define {required}()"
    return True, f"{kind}/{folder.name} matches the {required or kind} contract"


def scaffold(kind: str, name: str, *, root: Path | None = None) -> Path:
    """Write a working local component. Returns the folder."""
    if kind not in FOLDER_KINDS:
        raise ValueError(entry_point_hint(kind))
    folder = components_root(root) / kind / name
    folder.mkdir(parents=True, exist_ok=True)
    class_name = "".join(part.capitalize() for part in name.replace("-", "_").split("_")) or "Component"
    method = {
        "context": "async def assemble(self, request):\n        from iris_ai.sdk.types import ContextResult\n        return ContextResult()\n",
        "persona": "def text(self) -> str:\n        return 'A custom persona.'\n",
        "capture": "async def extract(self, request):\n        return []\n",
        "consolidator": "async def propose(self, request):\n        from iris_ai.sdk.types import ConsolidationPlan\n        return ConsolidationPlan()\n",
        "memory": (
            "async def connect(self):\n        return None\n\n"
            "    async def close(self):\n        return None\n\n"
            "    async def search(self, query, **kwargs):\n        return []\n\n"
            "    async def escalate(self, query, **kwargs):\n        return []\n\n"
            "    async def stats(self):\n        return {}\n\n"
            "    async def upsert_chunks(self, records):\n        return None\n\n"
            "    async def delete_file_chunks(self, path):\n        return None\n\n"
            "    async def replace_file_chunks(self, path, records):\n        return None\n\n"
            "    async def forget_entry(self, path, chunk_index):\n        return None\n\n"
            "    async def nearest(self, text, *, top_k=3):\n        return []\n\n"
            "    async def list_chunks(self):\n        return []\n"
        ),
    }.get(kind, "def ready(self) -> bool:\n        return True\n")
    (folder / "component.toml").write_text(
        f'kind = "{kind}"\nname = "{name}"\napi_version = "iris/v1"\nentry = "component:{class_name}"\n'
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
    """Move a staged component into place, select it, and pin its digest.

    The check runs in the jailed child. Importing the staged code in this
    process would execute it with the owner's privileges and could write
    bytecode into the folder before the digest is recorded.
    """
    from iris_ai.components.lock import pin

    staged = staging_dir(kind, name)
    if not (staged / "component.py").is_file():
        raise FileNotFoundError(f"nothing staged at {staged}")
    digest = component_digest(staged)
    ok, detail = check_in_sandbox(staged)
    if not ok:
        raise RuntimeError(detail)
    if component_digest(staged) != digest:
        raise RuntimeError("component digest changed during the check; activation refused")
    dest = components_root() / kind / name
    if dest.exists():
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = components_root() / ".backup" / kind / f"{name}-{stamp}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dest, backup)
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(staged), str(dest))
    from iris_ai.artifacts.store import ingest
    from iris_ai.lifecycle.journal import record

    stored = ingest(dest)
    _select(kind, name)
    meta = _read_toml(dest / "component.toml")
    pin(
        kind,
        name,
        source="local",
        digest=component_digest(dest),
        api_version=str(meta.get("api_version") or ""),
        permissions=list(meta.get("permissions") or []),
        approved_by="owner",
    )
    record("activate", kind=kind, name=name, digest=stored)
    return f"activated {kind}/{name}"


def rollback(kind: str) -> str:
    """Select the previous component for this kind."""
    from iris_ai.components.lock import set_selection

    _active, previous = rollback_target(kind)
    _select(kind, previous)
    set_selection(kind, previous)
    from iris_ai.lifecycle.journal import record

    record("rollback", kind=kind, name=previous)
    return f"{kind} rolled back to {previous}"


def note_failure(kind: str, name: str) -> str | None:
    """Count a runtime failure. Returns the rollback message, or None.

    A freshly pinned component is on probation: one failure while that window
    is open rolls the kind back. After the window, three failures do.
    """
    from iris_ai.components.lock import bump_fails, probation_left

    fails = bump_fails(kind, name)
    if probation_left(kind) > 0 or fails >= _FAIL_LIMIT:
        return rollback(kind)
    return None


class Guarded:
    """Call the component. After repeated failures, use the fallback."""

    def __init__(self, inner: Any, fallback: Any, *, kind: str, name: str) -> None:
        self._inner = inner
        self._fallback = fallback
        self.kind = kind
        self.name = name

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    async def assemble(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("assemble", *args, **kwargs)

    async def assemble_turn(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("assemble_turn", *args, **kwargs)

    async def extract(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("extract", *args, **kwargs)

    async def maybe_capture(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("maybe_capture", *args, **kwargs)

    async def sleep(self, *args: Any, **kwargs: Any) -> Any:
        return await self._call("sleep", *args, **kwargs)

    def close(self) -> None:
        inner = self._inner
        close = getattr(inner, "close", None)
        if close is not None:
            close()

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
        from iris_ai.components.lock import tick_probation

        target = self._inner
        try:
            result = await getattr(target, method)(*args, **kwargs)
        except Exception as exc:
            log.warning("%s %s failed: %s", self.kind, self.name, exc)
            switched = note_failure(self.kind, self.name)
            if switched:
                log.warning(switched)
            if self._fallback is not None and hasattr(self._fallback, method):
                return await getattr(self._fallback, method)(*args, **kwargs)
            raise
        else:
            tick_probation(self.kind)
            return result


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
    """Record a selection. Re-activating the current name must not point previous at itself."""
    from iris_ai.components.lock import kind_row, transaction

    with transaction() as data:
        row = kind_row(data, kind)
        current = str(row.get("active") or _builtin_default(kind))
        if current == name:
            previous = str(row.get("previous") or "")
            if not previous or previous == name:
                previous = _builtin_default(kind)
            row["previous"] = previous
        else:
            row["previous"] = current
            row["digest"] = ""
        row["active"] = name
        row["fails"] = 0


def _read_lock() -> dict:
    """Compatibility reader. Prefer `iris_ai.components.lock.read_lock`."""
    from iris_ai.components.lock import read_lock

    data = read_lock()
    flat = dict(data.get("kinds") or {})
    flat["version"] = data.get("version", 3)
    flat["kinds"] = data.get("kinds") or {}
    return flat


def _write_lock(data: dict) -> None:
    """Compatibility writer. A v1-shaped dict is stored as version 3."""
    from iris_ai.components.lock import normalize, write_lock

    write_lock(normalize(data))


def _read_toml(path: Path) -> dict:
    if not path.is_file():
        return {}
    import tomllib

    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return {}
