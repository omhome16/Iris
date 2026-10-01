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
    """Built-in names for one seam. A dotted path is always allowed as well."""
    return list(OPTIONS.get(kind, ()))


def _builtin(kind: str, name: str) -> Any:
    if kind == "context":
        return _CONTEXT.get(name)
    return None


def attach(runtime: Any, manifest: dict) -> None:
    """Set context_builder and capture_policy on the runtime from the manifest."""
    section = manifest.get("components") or {}
    if not isinstance(section, dict):
        log.warning("[components] must be a table; ignoring %r", section)
        return

    context = str(section.get("context") or "default")
    if context not in ("default", ""):
        builtin = _builtin("context", context)
        try:
            cls = builtin if builtin is not None else load_symbol(context)
        except ValueError as exc:
            log.warning("context %s: %s", context, exc)
            cls = None
        if cls is not None:
            runtime.context_builder = cls(runtime)
            log.info("context builder: %s", context)

    persona = str(section.get("persona") or "file")
    runtime.persona_choice = persona
    log.info("persona: %s", persona)

    capture = str(section.get("capture") or "default")
    if capture == "off":
        runtime.capture_policy = OffCapture()
        log.info("capture: off")
    elif capture not in ("default", ""):
        cls = load_symbol(capture)
        runtime.capture_policy = cls(runtime)
        log.info("capture: %s", capture)

    consolidator = str(section.get("consolidator") or "dreaming")
    if consolidator == "off":
        runtime.dreams = OffConsolidator()
        log.info("consolidator: off")
    elif consolidator not in ("dreaming", "default", ""):
        cls = load_symbol(consolidator)
        runtime.dreams = cls(runtime)
        log.info("consolidator: %s", consolidator)
