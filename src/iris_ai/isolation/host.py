"""Parent side of the component host.

The child is a separate interpreter process with a scrubbed environment. It
cannot import the Iris kernel. Calls it makes for memory, files, or a model
come back here and run only when the component's grant list allows them.

`COMPONENT_HOST=subprocess` sends context, capture, and consolidator through
this host. The default stays in-process. Persona and memory backends stay
in-process either way. Nothing here applies Landlock; the approval card and
`doctor` must not call it a kernel jail.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
from pathlib import Path
from typing import Any

from iris_ai.isolation.broker import Authority, CapabilityBroker
from iris_ai.isolation.rpc import error as rpc_error
from iris_ai.isolation.rpc import read_message, write_message
from iris_ai.isolation.rpc import request as rpc_request
from iris_ai.isolation.rpc import result as rpc_result
from iris_ai.sdk.types import ContextBlock, ContextResult

_HOSTMAIN = Path(__file__).resolve().parent / "hostmain.py"
_API = Path(__file__).resolve().parent / "component_api"
_KEEP = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
    "SYSTEMDRIVE",
    "COMSPEC",
    "LANG",
    "LC_ALL",
)


def _env(state: Path) -> dict[str, str]:
    env = {key: os.environ[key] for key in _KEEP if key in os.environ}
    root = str(state)
    env["HOME"] = root
    env["USERPROFILE"] = root
    env["TMPDIR"] = root
    env["TMP"] = root
    env["TEMP"] = root
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _meta(folder: Path) -> dict:
    path = folder / "component.toml"
    if not path.is_file():
        return {}
    loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


class ExtensionHost:
    """One child process. `request` blocks until that call returns."""

    def __init__(
        self,
        folder: Path,
        *,
        kind: str,
        name: str,
        handlers: dict | None = None,
        options: dict | None = None,
    ) -> None:
        self.folder = Path(folder)
        self.kind = kind
        self.name = name
        self.options = dict(options or {})
        meta = _meta(self.folder)
        self.authority = Authority([str(item) for item in (meta.get("permissions") or [])])
        self.broker = CapabilityBroker(self.authority, handlers or {})
        self._id = 0
        self._lock = threading.Lock()
        self._state = Path(tempfile.mkdtemp(prefix="iris-host-"))
        self._stderr = bytearray()
        self.proc = subprocess.Popen(
            [sys.executable, "-I", str(_HOSTMAIN), str(self.folder.resolve()), str(_API), str(self._state)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_env(self._state),
            cwd=str(self._state),
        )
        threading.Thread(target=self._drain, daemon=True).start()
        try:
            self.request(
                "initialize",
                {
                    "kind": kind,
                    "name": name,
                    "entry": str(meta.get("entry") or "component:Component"),
                    "options": self.options,
                    "grants": sorted(self.authority.grants),
                },
            )
        except Exception:
            self.close()
            raise

    def _drain(self) -> None:
        pipe = self.proc.stderr
        if pipe is None:
            return
        while True:
            chunk = pipe.read(4096)
            if not chunk:
                return
            self._stderr.extend(chunk)

    def request(self, method: str, params: dict | None = None, *, timeout: float = 15) -> Any:
        with self._lock:
            self._id += 1
            msg_id = self._id
            self._write(rpc_request(msg_id, method, params))
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self.close()
                    raise TimeoutError(f"component host timed out during {method}")
                message = self._read(remaining)
                if message.get("method") and message.get("id") != msg_id:
                    self._answer(message)
                    continue
                if message.get("id") != msg_id:
                    continue
                if "error" in message:
                    detail = str((message.get("error") or {}).get("message") or "component host failed")
                    tail = self._stderr.decode("utf-8", errors="replace").strip()
                    if tail:
                        detail = f"{detail} ({tail[-400:]})"
                    raise RuntimeError(detail)
                return message.get("result")

    def _answer(self, message: dict) -> None:
        try:
            value = self.broker.dispatch(str(message.get("method") or ""), message.get("params") or {})
        except Exception as exc:  # noqa: BLE001 - the child has to see a refusal
            self._write(rpc_error(message.get("id"), f"{type(exc).__name__}: {exc}"))
            return
        self._write(rpc_result(message.get("id"), value))

    def _write(self, payload: dict) -> None:
        pipe = self.proc.stdin
        if pipe is None:
            raise RuntimeError("component host is closed")
        write_message(pipe, payload)

    def _read(self, timeout: float) -> dict:
        holder: dict[str, Any] = {}

        def _run() -> None:
            try:
                pipe = self.proc.stdout
                if pipe is None:
                    holder["err"] = RuntimeError("component host is closed")
                    return
                holder["msg"] = read_message(pipe)
            except Exception as exc:  # noqa: BLE001 - surfaced as a host failure
                holder["err"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(timeout)
        if thread.is_alive():
            self.close()
            raise TimeoutError("component host timed out")
        if "err" in holder:
            tail = self._stderr.decode("utf-8", errors="replace").strip()
            extra = f" ({tail[-400:]})" if tail else ""
            raise RuntimeError(f"component host exited: {holder['err']}{extra}")
        return holder["msg"]

    def close(self) -> None:
        proc = getattr(self, "proc", None)
        if proc is None or proc.poll() is not None:
            return
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=2)

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001 - a dropped host still has to die
            return


class HostedComponent:
    """The object the loader keeps. Methods match the in-process component."""

    def __init__(self, host: ExtensionHost) -> None:
        self._host = host

    async def assemble(self, request: Any) -> ContextResult:
        payload = await asyncio.to_thread(
            self._host.request,
            "call",
            {
                "name": "assemble",
                "request": {
                    "message": str(getattr(request, "message", "") or ""),
                    "session_id": str(getattr(request, "session_id", "") or ""),
                    "origin": str(getattr(request, "origin", "owner") or "owner"),
                },
            },
        )
        return _context(payload or {})

    def text(self) -> str:
        payload = self._host.request("call", {"name": "text", "request": {}})
        return str((payload or {}).get("text") or "")

    def call(self, name: str, request: dict | None = None) -> Any:
        return self._host.request("call", {"name": name, "request": dict(request or {})})

    def close(self) -> None:
        self._host.close()


def open_component(
    folder: Path,
    *,
    kind: str,
    name: str,
    handlers: dict | None = None,
    options: dict | None = None,
) -> HostedComponent:
    return HostedComponent(ExtensionHost(folder, kind=kind, name=name, handlers=handlers, options=options))


def _context(payload: dict) -> ContextResult:
    if payload.get("type") == "text":
        return ContextResult(blocks=(ContextBlock(title="", text=str(payload.get("text") or "")),))
    blocks = tuple(
        ContextBlock(
            title=str(block.get("title") or ""),
            text=str(block.get("text") or ""),
            source=str(block.get("source") or ""),
            kind=str(block.get("kind") or "memory"),
            priority=int(block.get("priority") or 50),
        )
        for block in payload.get("blocks") or []
        if isinstance(block, dict)
    )
    return ContextResult(blocks=blocks, skills=tuple(str(item) for item in (payload.get("skills") or [])))
