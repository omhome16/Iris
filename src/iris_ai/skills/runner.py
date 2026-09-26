"""Running a skill's script — the boundary, not the capability.

The capability is small on purpose. A skill may ship code under its own
`scripts/` directory, and this module is the only thing that runs it, under four
constraints that are enforced here rather than promised in docs:

1. **Resolution.** The path must land inside *that skill's* `scripts/` directory.
   Traversal, absolute paths, drive letters, backslashes and symlink escapes are
   rejected. A flat learned skill has no script directory, so it has no code.
2. **A pre-screen that informs rather than decides.** `pre_screen()` is
   deterministic and cheap: it finds credential access, networking, process
   spawning, dynamic execution and filesystem escapes. Its findings do not block
   — they ride into the owner's approval prompt, so approval is informed.
3. **A child environment built from scratch.** Not "cleaned up": constructed.
   Only `PATH`, a locale and a temp directory survive, and `HOME` points at the
   skill directory. A script cannot read a key that was never handed to it.
4. **Bounds.** A timeout (the manifest's, capped by config) kills the process; the
   output is capped so a runaway loop cannot fill the trace or the prompt.

What this is *not*: kernel isolation. A Python process running as the same OS
user can still read what that user can read, and can still open a socket. That
is why the judgment gate and the approval sit on the path *before* execution,
and why the environment carries no secrets. Stronger isolation is a deployment
concern — see `docs/deployment.md`.

Two rules worth knowing from the industry guidance this follows (NVIDIA's
sandboxing guidance, the OWASP agent cheat sheet, and Trail of Bits' 2025
prompt-injection-to-RCE write-up): never pass untrusted work a shell, and never
treat approval as the only control. Hence `argv` (never `shell=True`) and a
judgment gate that can refuse outright.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.skills import Skill

# What a script is allowed to inherit. Everything else — every API key, every
# IRIS_* setting, the .env contents — simply does not exist in the child.
_ENV_ALLOWLIST = (
    "PATH",
    "PATHEXT",
    "SYSTEMROOT",  # Windows needs it to start a process at all
    "SYSTEMDRIVE",
    "WINDIR",
    "COMSPEC",
    "LANG",
    "LC_ALL",
    "PYTHONIOENCODING",
    "PYTHONUTF8",
)

# Findings by the *code* that would cause them, not by text that mentions them.
# Parsing matters here: a first version scanned the raw source, and the shipped
# `extract.py` was flagged "uses the network" because its docstring says the
# script is offline — a false finding that then travelled into the judgment's
# state and dragged a safe script below the gate. Scanning the AST fixes that
# class of bug at the source.
# Modules whose *import or use* is worth telling the owner about, by label.
_SUSPECT_MODULES: dict[str, str] = {
    "socket": "uses the network",
    "ssl": "uses the network",
    "requests": "uses the network",
    "httpx": "uses the network",
    "urllib": "uses the network",
    "http": "uses the network",
    "smtplib": "uses the network",
    "ftplib": "uses the network",
    "subprocess": "spawns processes",
    "pty": "spawns processes",
    "pickle": "uses dynamic execution",
    "marshal": "uses dynamic execution",
    "ctypes": "uses dynamic execution",
    "dotenv": "reads credentials/environment",
    "shutil": "touches the filesystem",
}
# `os` is the one module that means different things by attribute.
_OS_LABELS = {
    "environ": "reads credentials/environment",
    "getenv": "reads credentials/environment",
    "putenv": "reads credentials/environment",
    "system": "spawns processes",
    "popen": "spawns processes",
    "execv": "spawns processes",
    "execvp": "spawns processes",
    "remove": "touches the filesystem",
    "unlink": "touches the filesystem",
    "rmdir": "touches the filesystem",
    "rename": "touches the filesystem",
}
_ENV_NAMES = {"getenv", "putenv"}
_DYNAMIC_NAMES = {"eval", "exec", "compile", "__import__", "globals", "locals"}
_DESTRUCTIVE_NAMES = {"remove", "unlink", "rmdir", "rmtree", "move", "rename"}
# Kept for files that are not Python (the spec's scripts are, but a skill may
# ship a shell script too, and "we could not parse it" must not mean "clean").
_FALLBACK_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"os\.environ|getenv|dotenv|\.env\b"), "reads credentials/environment"),
    (re.compile(r"\bsocket\b|\brequests\b|\burllib\b|\bhttpx\b|\bcurl\b|\bwget\b"), "uses the network"),
    (re.compile(r"subprocess|os\.system|\bpopen\b|\bspawn\b"), "spawns processes"),
    (re.compile(r"\beval\s*\(|\bexec\s*\(|__import__|\bcompile\s*\("), "uses dynamic execution"),
    (re.compile(r"\brm\s+-rf\b|shutil\.rmtree|os\.remove|open\s*\(\s*['\"]/"), "touches the filesystem"),
)


class ScriptError(Exception):
    """The script cannot be run at all (wrong place, wrong skill, missing file)."""


@dataclass(frozen=True, slots=True)
class ScriptResult:
    ok: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    error: str = ""
    findings: list[str] = field(default_factory=list)


def _dotted(node: ast.AST) -> str:
    """`os.environ` from `Attribute(value=Name('os'), attr='environ')`."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def pre_screen(source: str) -> list[str]:
    """Deterministic findings for the owner's eyes. Never blocks by itself.

    Only real code counts: comments, docstrings and prose in the skill's own
    documentation are not evidence of anything.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return [label for pattern, label in _FALLBACK_PATTERNS if pattern.search(source)]

    findings: set[str] = set()
    for node in ast.walk(tree):
        # Imports: `import socket`, `from urllib import request`.
        for alias in getattr(node, "names", []):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
                root = module.split(".")[0]
                if root in _SUSPECT_MODULES:
                    findings.add(_SUSPECT_MODULES[root])
        if isinstance(node, ast.Call):
            # The callee's own name, so `open('/etc/x').read()` is judged too.
            callee = _dotted(node.func) if isinstance(node.func, ast.Attribute) else (
                node.func.id if isinstance(node.func, ast.Name) else ""
            )
            if _opens_outside(callee, node):
                findings.add("touches the filesystem")
        name = _dotted(node) if isinstance(node, ast.Attribute) else (
            node.id if isinstance(node, ast.Name) else ""
        )
        if not name:
            continue
        head, _, tail = name.partition(".")
        if head == "os" and tail in _OS_LABELS:
            findings.add(_OS_LABELS[tail])
        elif head in _SUSPECT_MODULES:
            findings.add(_SUSPECT_MODULES[head])
        if name in _ENV_NAMES or name in _DYNAMIC_NAMES:
            findings.add("reads credentials/environment" if name in _ENV_NAMES else "uses dynamic execution")
        if "." in name and tail in _DESTRUCTIVE_NAMES:
            findings.add("touches the filesystem")
    return sorted(findings)


def _opens_outside(fname: str, call: ast.Call) -> bool:
    """`open()` with a literal path that leaves the current directory.

    Only literal strings are judged: a path the script computes is a judgment
    call for the owner and the model to make, not for a regex.
    """
    if fname not in {"open", "os.open", "io.open"} or not call.args:
        return False
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.startswith(("/", "~")) or ".." in first.value
    return False


def resolve_script(skill: Skill, script: str) -> Path:
    """The script's real path, or `ScriptError`. Never trusts the caller's string."""
    if not skill.root:
        raise ScriptError(f"skill {skill.name!r} has no script directory (it is a learned procedure)")
    if not script or "\\" in script or "\x00" in script:
        raise ScriptError(f"unsafe script path {script!r}")
    root = Path(skill.root).resolve()
    scripts_dir = (root / "scripts").resolve()
    if Path(script).is_absolute() or Path(script).drive:
        raise ScriptError(f"unsafe script path {script!r}: absolute paths are not allowed")
    if any(part in ("..", "~") for part in Path(script).parts):
        raise ScriptError(f"unsafe script path {script!r}: traversal is not allowed")
    resolved = (root / script).resolve()
    if not resolved.is_relative_to(scripts_dir):
        raise ScriptError(f"{script!r} is not inside the skill's scripts/ directory")
    if not resolved.is_file():
        raise ScriptError(f"{script!r} does not exist")
    return resolved


def _python() -> str:
    """The interpreter running Iris, so a skill's script shares its stdlib."""
    return sys.executable


def _child_env(workdir: Path) -> dict[str, str]:
    """A minimal environment, built rather than cleaned."""
    env = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    env["HOME"] = str(workdir)
    env["USERPROFILE"] = str(workdir)  # Windows' HOME
    env["TMPDIR"] = str(workdir)
    env["TMP"] = str(workdir)
    env["TEMP"] = str(workdir)
    env["PYTHONPATH"] = ""  # never inherit the parent's import path
    return env


def _cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… truncated ({len(text) - limit} more chars)"


async def run_script(
    skill: Skill,
    script: str,
    args: Sequence[str] = (),
    *,
    timeout: float | None = None,
    max_output: int | None = None,
) -> ScriptResult:
    """Run one script of one skill under the boundary described above.

    Returns data, never raises for a script that fails: a non-zero exit, a
    timeout and a crash are all normal outcomes the model should see.
    """
    try:
        path = resolve_script(skill, script)
    except ScriptError as exc:
        return ScriptResult(ok=False, error=str(exc))

    workdir = path.parent.parent
    cap = max_output if max_output is not None else settings.skill_script_max_output_chars
    ceiling = min(
        timeout if timeout is not None else skill.timeout_seconds,
        settings.skill_script_timeout_seconds,
    )
    ceiling = max(0.1, float(ceiling))
    findings = pre_screen(path.read_text(encoding="utf-8", errors="replace"))

    # Deliberately a worker thread around `subprocess.run`, *not*
    # `asyncio.create_subprocess_exec`: the API process selects the Windows
    # Selector loop (psycopg cannot run on Proactor) where asyncio subprocess
    # support is unimplemented. Running a skill's script must not be the one
    # feature that only works in some event loops.
    argv = [_python(), str(path), *[str(a) for a in args]]
    try:
        completed = await asyncio.to_thread(
            subprocess.run,
            argv,
            cwd=str(workdir),
            env=_child_env(workdir),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=ceiling,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # `subprocess.run` has already killed and reaped the child by the time it
        # raises, so there is nothing left here to clean up.
        return ScriptResult(
            ok=False,
            timed_out=True,
            error=f"timed out after {ceiling:g}s",
            stdout=_cap(_text(exc.stdout), cap),
            stderr=_cap(_text(exc.stderr), cap),
            findings=findings,
        )
    except OSError as exc:
        return ScriptResult(ok=False, error=f"could not start the script: {exc}", findings=findings)

    stdout = _cap(_text(completed.stdout), cap)
    stderr = _cap(_text(completed.stderr), cap)
    ok = completed.returncode == 0
    return ScriptResult(
        ok=ok,
        stdout=stdout,
        stderr=stderr,
        exit_code=completed.returncode,
        error="" if ok else f"exited with code {completed.returncode}",
        findings=findings,
    )


def _text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else raw.decode("utf-8", errors="replace")
