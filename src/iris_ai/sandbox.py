"""Sandbox — the only place Iris may touch the file system.

Iris's design rule is *no host access*: memory, skills, dreams only. The
sandbox is the single, deliberate exception: a jailed directory (default
`workspace/sandbox/`) where she may create, read, write and list files.

Every path is resolved against the sandbox root and rejected if it escapes
(`..`, absolute paths, drive letters). Iris physically cannot touch anything
outside her box — `.env`, system files, the workspace itself: all unreachable.
"""

from __future__ import annotations

import re
from pathlib import Path

MAX_FILE_BYTES = 200_000  # read cap — a file larger than this is truncated
MAX_WRITE_CHARS = 50_000  # write cap — keep writes human-sized

# Host-OS independent attack strings: pathlib parses backslashes as literal
# filename characters on POSIX (a harmless oddly-named file inside the box),
# but the validator must reject them regardless so behaviour is identical on
# every host (and to encode Windows-style traversal attacks).
_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class SandboxError(Exception):
    pass


class Sandbox:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, rel: str) -> Path:
        """Validate + resolve a sandbox-relative path. Raises SandboxError
        on any traversal attempt (.., absolute, drive, symlink escape)."""
        if not rel or rel in (".", "/", "\\"):
            return self.root
        if "\\" in rel:
            raise SandboxError("backslashes are not allowed in sandbox paths")
        if _DRIVE_RE.match(rel):
            raise SandboxError("drive-letter paths are not allowed")
        p = Path(rel)
        if p.is_absolute() or p.drive:
            raise SandboxError("absolute paths are not allowed")
        if any(part in ("..", "~") for part in p.parts):
            raise SandboxError("path traversal is not allowed")
        resolved = (self.root / p).resolve()
        if not resolved.is_relative_to(self.root.resolve()):
            raise SandboxError("path escapes the sandbox")
        return resolved

    def create(self, rel: str, content: str) -> Path:
        path = self.resolve(rel)
        if path.exists():
            raise SandboxError(f"already exists: {rel}")
        self._write(path, content)
        return path

    def write(self, rel: str, content: str) -> Path:
        path = self.resolve(rel)
        self._write(path, content)
        return path

    def read(self, rel: str) -> str:
        path = self.resolve(rel)
        if not path.is_file():
            raise SandboxError(f"not a file: {rel}")
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            data = data[:MAX_FILE_BYTES]
        return data.decode("utf-8", errors="replace")

    def list(self, rel: str = "") -> list[dict]:
        path = self.resolve(rel)
        if not path.is_dir():
            raise SandboxError(f"not a directory: {rel or '.'}")
        entries = []
        for p in sorted(path.rglob("*")):
            if p.is_file():
                entries.append(
                    {
                        "path": p.relative_to(self.root).as_posix(),
                        "bytes": p.stat().st_size,
                    }
                )
        return entries

    def _write(self, path: Path, content: str) -> None:
        if len(content) > MAX_WRITE_CHARS:
            raise SandboxError(f"content too long (max {MAX_WRITE_CHARS} chars)")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
