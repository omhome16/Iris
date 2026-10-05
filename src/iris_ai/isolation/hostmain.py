"""Child process for one approved component.

Run this file with the interpreter. It must not import the Iris kernel. The
parent passes the artifact directory, the small component API directory, and a
writable state directory. Provider keys are not in the environment. The kernel
is removed from `sys.path` before the component is imported, and an audit hook
refuses reads outside the interpreter, the artifact, the component API, and
the state directory.

This is an audit boundary. It is not Landlock or seccomp. Those still apply
only to the jailed check, and only on Linux.
"""

from __future__ import annotations

import asyncio
import importlib.util
import inspect
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


def _dup_stdout():
    """Component prints must not corrupt the frame stream."""
    saved = os.dup(1)
    os.dup2(2, 1)
    return os.fdopen(saved, "wb", buffering=0)


def _apply_linux_jail(artifact: Path, api: Path, state: Path) -> str:
    """Landlock when this is Linux. Anywhere else the label is ``audit``."""
    path = Path(__file__).resolve().with_name("linux_jail.py")
    spec = importlib.util.spec_from_file_location("_iris_linux_jail", path)
    if spec is None or spec.loader is None:
        return "audit"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        return str(module.apply(artifact, [Path(sys.base_prefix), Path(sys.prefix), artifact, api, state]))
    except Exception:  # noqa: BLE001 - a jail that cannot install leaves the audit hook
        return "audit"


def _drop_kernel() -> None:
    """Remove every path entry that can import the real Iris package."""
    removed: list[str] = []
    for entry in list(sys.path):
        if not entry:
            removed.append(entry)
            continue
        root = Path(entry)
        norm = entry.replace("\\", "/").lower()
        kernel = (root / "iris_ai" / "config.py").is_file() or (root / "iris_ai" / "agent" / "runtime.py").is_file()
        packaged = norm.endswith("/site-packages") or "/site-packages/" in norm
        if kernel or packaged:
            removed.append(entry)
    for entry in removed:
        while entry in sys.path:
            sys.path.remove(entry)


def _norm(path: object) -> str:
    return os.path.normcase(os.path.abspath(os.fsdecode(path)))  # type: ignore[arg-type]


def _inside(path: object, roots: list[str]) -> bool:
    try:
        text = _norm(path)
    except (TypeError, ValueError, OSError):
        return False
    return any(text == root or text.startswith(root + os.sep) for root in roots)


def _install_audit(read_roots: list[Path], write_roots: list[Path]) -> None:
    readable = [_norm(path) for path in read_roots]
    writable = [_norm(path) for path in write_roots]
    native = {"ctypes", "_ctypes", "cffi", "_cffi_backend"}

    def _audit(event: str, args: tuple) -> None:
        if event == "import" and args:
            name = str(args[0] or "")
            root = name.split(".", 1)[0]
            if name in native or root in native:
                raise PermissionError(f"native loader blocked in component host: {name}")
            return
        if event in {"os.system", "os.fork", "os.posix_spawn"} or event.startswith(("os.exec", "subprocess.")):
            raise PermissionError(f"process spawn blocked in component host: {event}")
        if event in {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname"}:
            raise PermissionError(f"network blocked in component host: {event}")
        if event not in {"open", "os.open"}:
            return
        target = args[0] if args else None
        if isinstance(target, int) or target is None:
            return
        mode = args[1] if len(args) > 1 else "r"
        flags = args[2] if len(args) > 2 else 0
        writing = isinstance(mode, str) and any(flag in mode for flag in "wax+")
        if isinstance(flags, int):
            writing = writing or bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT))
        roots = writable if writing else readable
        if not _inside(target, roots):
            raise PermissionError(f"{'write' if writing else 'read'} outside component host: {target}")

    sys.addaudithook(_audit)


def _read_exact(stream, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            raise EOFError("parent closed the pipe")
        buf.extend(chunk)
    return bytes(buf)


def _read_message(stream) -> dict:
    import struct

    header = _read_exact(stream, 4)
    (size,) = struct.unpack(">I", header)
    if size > 1024 * 1024:
        raise ValueError("message exceeds 1MB")
    payload = json.loads(_read_exact(stream, size))
    if not isinstance(payload, dict):
        raise ValueError("message must be a JSON object")
    return payload


def _write_message(stream, payload: dict) -> None:
    import struct

    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > 1024 * 1024:
        raise ValueError("message exceeds 1MB")
    stream.write(struct.pack(">I", len(body)))
    stream.write(body)
    stream.flush()


def _load(folder: Path, entry: str):
    module_name, _, attr = entry.partition(":")
    attr = attr or "Component"
    path = folder / f"{module_name or 'component'}.py"
    spec = importlib.util.spec_from_file_location("iris_hosted_component", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cls = getattr(module, attr)
    return cls


def _construct(cls, ctx):
    params = [
        item
        for item in inspect.signature(cls).parameters.values()
        if item.name != "self" and item.kind in (item.POSITIONAL_ONLY, item.POSITIONAL_OR_KEYWORD)
    ]
    required = [item for item in params if item.default is inspect.Parameter.empty]
    if any(item.name in {"ctx", "context"} for item in required[:1]):
        return cls(ctx)
    if required:
        raise TypeError("a hosted component must take ctx, or no required arguments")
    return cls()


def _dump(value) -> dict:
    if isinstance(value, str):
        return {"type": "text", "text": value}
    blocks = getattr(value, "blocks", None)
    if blocks is None:
        return {"type": "text", "text": str(value)}
    return {
        "type": "context",
        "blocks": [
            {
                "title": str(getattr(block, "title", "") or ""),
                "text": str(getattr(block, "text", "") or ""),
                "source": str(getattr(block, "source", "") or ""),
                "kind": str(getattr(block, "kind", "memory") or "memory"),
                "priority": int(getattr(block, "priority", 50) or 50),
            }
            for block in blocks
        ],
        "skills": [str(item) for item in (getattr(value, "skills", ()) or ())],
    }


def main() -> None:
    out = _dup_stdout()
    artifact = Path(sys.argv[1]).resolve()
    api = Path(sys.argv[2]).resolve()
    state = Path(sys.argv[3]).resolve()
    state.mkdir(parents=True, exist_ok=True)
    # The Windows proactor loop opens a self-pipe. Do that before the audit
    # hook, or creating the loop looks like a blocked socket.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _drop_kernel()
    _apply_linux_jail(artifact, api, state)
    read_roots = [Path(sys.base_prefix), Path(sys.prefix), Path(sys.executable).resolve().parent, artifact, api, state]
    _install_audit(read_roots, [state])
    sys.path.insert(0, str(api))
    os.chdir(state)

    incoming = sys.stdin.buffer
    calls = {"n": 0}

    def broker(method: str, params: dict):
        calls["n"] += 1
        msg_id = f"c{calls['n']}"
        _write_message(out, {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        while True:
            reply = _read_message(incoming)
            if reply.get("id") != msg_id:
                continue
            if "error" in reply:
                message = str((reply.get("error") or {}).get("message") or "broker refused")
                raise RuntimeError(message)
            return reply.get("result")

    import iris_ai.sdk.context as context_mod

    context_mod.set_broker(broker)
    instance = None

    while True:
        try:
            message = _read_message(incoming)
        except EOFError:
            return
        msg_id = message.get("id")
        method = str(message.get("method") or "")
        params = message.get("params") or {}
        try:
            if method == "initialize":
                ctx = context_mod.ComponentContext(
                    api_version="iris/v1",
                    kind=str(params.get("kind") or ""),
                    name=str(params.get("name") or ""),
                    **dict(params.get("options") or {}),
                )
                instance = _construct(_load(artifact, str(params.get("entry") or "component:Component")), ctx)
                value = {"ok": True}
            elif method == "call":
                if instance is None:
                    raise RuntimeError("component is not initialized")
                name = str(params.get("name") or "")
                request = SimpleNamespace(**dict(params.get("request") or {}))
                target = getattr(instance, name)
                value = target() if name == "text" else target(request)
                if inspect.isawaitable(value):
                    value = loop.run_until_complete(value)
                value = _dump(value)
            elif method == "shutdown":
                _write_message(out, {"jsonrpc": "2.0", "id": msg_id, "result": {"ok": True}})
                return
            else:
                raise RuntimeError(f"unknown method {method}")
            _write_message(out, {"jsonrpc": "2.0", "id": msg_id, "result": value})
        except Exception as exc:  # noqa: BLE001 - the parent has to see the failure
            _write_message(
                out,
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}"},
                },
            )


if __name__ == "__main__":
    main()
