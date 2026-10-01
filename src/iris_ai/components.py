"""Load `[components]` from the harness manifest.

Built-in names keep today's behaviour. A dotted path imports the caller's class.
`off` disables capture or consolidation.
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

log = logging.getLogger("iris.components")


class OffCapture:
    async def maybe_capture(self, *, user_message: str, reply: str, known_context: str) -> str:
        return ""


class OffConsolidator:
    async def sleep(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            staged=0, promoted=0, themes=[], added=0, superseded=0, fallback=True
        )


def load_symbol(spec: str) -> type:
    """`pkg.mod:Class` -> the class."""
    if ":" not in spec:
        raise ValueError(f"component {spec!r} needs a module path and a class, like pkg.mod:Class")
    module_name, _, attr = spec.partition(":")
    module = importlib.import_module(module_name)
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ValueError(f"{spec!r} has no attribute {attr}") from exc


class RecallFirst:
    """The default prefix, with a memory search placed in front of it."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        from iris_ai.agent.context import ContextAssembler

        text, skills = await ContextAssembler(self.runtime).assemble_turn(
            user_message, session_id=session_id
        )
        try:
            hits = await self.runtime.index.search(user_message, top_k=5)
        except Exception:  # noqa: BLE001 - recall is optional; the turn still runs
            hits = []
        if hits:
            lines = [f"- {hit.path}: {str(hit.content)[:200]}" for hit in hits[:5]]
            text = "## Recalled\n" + "\n".join(lines) + "\n\n" + text
        return text, skills


class MinimalContext:
    """No memory and no skills. The smallest prompt that still answers."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        return "Answer in as few tokens as you can. Do not search memory.", []


OPTIONS: dict[str, tuple[str, ...]] = {
    "context": ("default", "recall-first", "minimal"),
    "memory": ("sqlite", "markdown", "pgvector"),
    "persona": ("blank", "file", "assistant", "coder", "researcher", "tutor"),
    "capture": ("default", "off"),
    "consolidator": ("dreaming", "off"),
    "channel": ("terminal", "telegram", "http"),
}

_CONTEXT = {
    "default": None,
    "recall-first": RecallFirst,
    "minimal": MinimalContext,
}


def list_options(kind: str) -> list[str]:
    """Built-in names plus local folders. A dotted path is always allowed too."""
    from iris_ai.plug import list_local

    names = list(OPTIONS.get(kind, ()))
    for info in list_local(kind):
        if info.name not in names:
            names.append(info.name)
    return names


def _builtin(kind: str, name: str) -> Any:
    if kind == "context":
        return _CONTEXT.get(name)
    return None


def _resolve_class(kind: str, name: str) -> Any:
    """Built-in, local folder, or dotted path. None when this name is a mode."""
    from iris_ai.plug import load_class, local_folder

    folder = local_folder(kind, name)
    if folder is not None:
        return load_class(folder), "local"
    builtin = _builtin(kind, name)
    if builtin is not None:
        return builtin, "builtin"
    if ":" in name:
        return load_symbol(name), "dotted"
    return None


def _install(runtime: Any, kind: str, name: str, *, fallback: Any = None) -> Any:
    """Build one component. A failure logs one line and returns the fallback."""
    from iris_ai.plug import Guarded, construct

    resolved = None
    try:
        resolved = _resolve_class(kind, name)
    except Exception as exc:  # noqa: BLE001 - a bad component must not stop boot
        log.warning("%s %s failed to load (%s). Using the built-in.", kind, name, exc)
        return fallback
    if resolved is None:
        return fallback
    cls, source = resolved
    try:
        inner = construct(cls, runtime)
    except Exception as exc:  # noqa: BLE001 - construction errors are reported, not fatal
        log.warning("%s %s failed to start (%s). Using the built-in.", kind, name, exc)
        return fallback
    # Local components are the ones Iris or the owner just added. Watch those.
    # A dotted path stays the class the caller constructed.
    if source == "local" and fallback is not None and kind != "persona":
        return Guarded(inner, fallback, kind=kind, name=name)
    return inner


def attach(runtime: Any, manifest: dict) -> None:
    """Set context_builder and capture_policy on the runtime from the manifest."""
    section = manifest.get("components") or {}
    if not isinstance(section, dict):
        log.warning("[components] must be a table; ignoring %r", section)
        return

    context = str(section.get("context") or "default")
    if context not in ("default", ""):
        from iris_ai.agent.context import ContextAssembler

        built = _install(runtime, "context", context, fallback=ContextAssembler(runtime))
        if built is not None:
            runtime.context_builder = built
            log.info("context builder: %s", context)
        else:
            log.warning("context %s is not available. Using the built-in assembler.", context)

    persona = str(section.get("persona") or "file")
    runtime.persona_choice = persona
    if (persona not in ("file", "blank", "") and ":" in persona) or _looks_local("persona", persona):
        built = _install(runtime, "persona", persona)
        if built is not None:
            runtime.persona_source = built
    log.info("persona: %s", persona)

    capture = str(section.get("capture") or "default")
    if capture == "off":
        runtime.capture_policy = OffCapture()
        log.info("capture: off")
    elif capture not in ("default", ""):
        built = _install(runtime, "capture", capture, fallback=None)
        if built is not None:
            runtime.capture_policy = built
            log.info("capture: %s", capture)
        else:
            log.warning("capture %s is not available. Using the built-in.", capture)

    consolidator = str(section.get("consolidator") or "dreaming")
    if consolidator == "off":
        runtime.dreams = OffConsolidator()
        log.info("consolidator: off")
    elif consolidator not in ("dreaming", "default", ""):
        built = _install(runtime, "consolidator", consolidator, fallback=runtime.dreams)
        if built is not None:
            runtime.dreams = built
            log.info("consolidator: %s", consolidator)


def _looks_local(kind: str, name: str) -> bool:
    from iris_ai.plug import local_folder

    return local_folder(kind, name) is not None
