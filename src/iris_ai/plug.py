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
from dataclasses import dataclass, field
from datetime import datetime
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


def component_digest(folder: Path) -> str:
    """Stable sha256 of every file in a component folder."""
    digest = hashlib.sha256()
    if not folder.is_dir():
        return digest.hexdigest()
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    for path in files:
        digest.update(path.relative_to(folder).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def rollback_target(kind: str) -> tuple[str, str]:
    """`(active, previous)` for a kind. Previous falls back to the built-in."""
    entry = _read_lock().get(kind) or {}
    active = str(entry.get("active") or "")
    previous = str(entry.get("previous") or "") or _builtin_default(kind)
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
import json, os, runpy, sys
from pathlib import Path

sandbox = Path(sys.argv[1]).resolve()
read_roots = [sandbox]
for item in json.loads(sys.argv[2]):
    read_roots.append(Path(item).resolve())
os.chdir(sandbox)

def _kernel_isolation(folder, roots):
    # Landlock denies exec, outside writes (including utime), and TCP.
    # Seccomp denies execve and any new socket, which is what stops
    # ctypes.CDLL(None).system and DNS. The audit hook below still raises
    # PermissionError for the Python-level calls. Where the kernel refuses
    # Landlock, the hook plus an import ban is all that remains.
    label = "audit"
    try:
        import ctypes
        from ctypes import Structure, c_int, c_uint64, c_void_p
        libc = ctypes.CDLL(None, use_errno=True)

        def _ok(ret, what):
            if ret < 0:
                err = ctypes.get_errno()
                raise OSError(err, f"{what}: {os.strerror(err)}")
            return ret

        if not sys.platform.startswith("linux"):
            raise OSError("landlock is linux-only")
        fs_read = (1 << 2) | (1 << 3)
        fs_write = (
            (1 << 1) | (1 << 4) | (1 << 5) | (1 << 6) | (1 << 7) | (1 << 8)
            | (1 << 9) | (1 << 10) | (1 << 11) | (1 << 12) | (1 << 13) | (1 << 14)
        )
        fs_handled = fs_read | fs_write | (1 << 0)

        class RulesetAttr(Structure):
            _fields_ = [("handled_access_fs", c_uint64), ("handled_access_net", c_uint64)]

        class PathBeneath(Structure):
            _fields_ = [("allowed_access", c_uint64), ("parent_fd", c_int)]

        attr = RulesetAttr(fs_handled, 3)
        rules = _ok(libc.syscall(444, ctypes.byref(attr), ctypes.sizeof(attr), 0), "landlock")
        seen = set()
        # /usr and the loader dirs are how Python imports. /etc is not in this
        # list: a native open() would bypass the audit hook, and Landlock would
        # otherwise allow reading it. /proc and /dev stay readable so the
        # interpreter can start; they are not writable.
        for item in [*roots, "/usr", "/lib", "/lib64", "/proc", "/dev", str(folder)]:
            path = Path(item)
            if not path.exists():
                continue
            resolved = path.resolve()
            key = str(resolved)
            if key in seen:
                continue
            seen.add(key)
            access = fs_read | fs_write if resolved == folder else fs_read
            handle = os.open(key, os.O_PATH | getattr(os, "O_CLOEXEC", 0))
            rule = PathBeneath(access, handle)
            try:
                _ok(libc.syscall(445, rules, 1, ctypes.byref(rule), 0), key)
            except OSError:
                continue
            finally:
                os.close(handle)
        _ok(libc.prctl(38, 1, 0, 0, 0), "no_new_privs")
        _ok(libc.syscall(446, rules, 0), "landlock restrict")
        os.close(rules)
        label = "landlock"
        try:
            import struct
            blocked = {
                0xC000003E: (59, 322, 41, 42, 43, 44, 45, 46, 47, 49, 50, 53, 288, 299, 307, 101, 310, 311),
                0xC00000B7: (221, 281, 198, 203, 202, 206, 207, 211, 212, 200, 201, 199, 242, 243, 269, 117, 270, 271),
            }
            machine = struct.calcsize("P")
            arch = 0xC000003E if machine == 8 and sys.byteorder == "little" else 0
            # aarch64 is also 64-bit little-endian; platform tells them apart.
            import platform
            if platform.machine() in {"aarch64", "arm64"}:
                arch = 0xC00000B7
            elif platform.machine() in {"x86_64", "amd64"}:
                arch = 0xC000003E
            numbers = blocked.get(arch)
            if numbers:
                def stmt(code, k):
                    return struct.pack("HBBI", code, 0, 0, k & 0xFFFFFFFF)
                def jump(code, k, jt, jf):
                    return struct.pack("HBBI", code, jt, jf, k & 0xFFFFFFFF)
                allow, errno_ret = 0x7FFF0000, 0x00050000 | 1
                kill = 0x80000000
                instr = [
                    stmt(0x20, 4),
                    jump(0x15, arch, 1, 0),
                    stmt(0x06, kill),
                    stmt(0x20, 0),
                ]
                for number in numbers:
                    instr.append(jump(0x15, number, 0, 1))
                    instr.append(stmt(0x06, errno_ret))
                instr.append(stmt(0x06, allow))
                blob = b"".join(instr)
                buf = ctypes.create_string_buffer(blob)

                class Prog(Structure):
                    _fields_ = [("len", ctypes.c_ushort), ("filt", c_void_p)]

                prog = Prog(len(blob) // 8, ctypes.cast(buf, c_void_p))
                _ok(libc.prctl(22, 2, ctypes.byref(prog)), "seccomp")
                label = "landlock+seccomp"
        except (OSError, struct.error):
            pass
    except (OSError, AttributeError):
        label = "audit"
    finally:
        sys.modules.pop("ctypes", None)
        sys.modules.pop("_ctypes", None)
    if os.environ.get("IRIS_CHECK_NETNS") == "1" and label != "audit":
        label += "+netns"
    elif os.environ.get("IRIS_CHECK_NETNS") == "1":
        label = "audit+netns"
    return label

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
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = components_root() / ".backup" / kind / f"{name}-{stamp}"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(dest, backup)
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
    active = str(entry.get("active") or "")
    previous = str(entry.get("previous") or "")
    if not previous or previous == active:
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
    entry = data.get(kind) or {}
    current = str(entry.get("active") or _builtin_default(kind))
    if current == name:
        # Re-activating the component that is already selected must not point
        # `previous` at itself, or rollback reports success and changes nothing.
        previous = str(entry.get("previous") or "")
        if not previous or previous == name:
            previous = _builtin_default(kind)
    else:
        previous = current
    data[kind] = {"active": name, "previous": previous, "fails": 0}
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
