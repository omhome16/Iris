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


def attach(runtime: Any, manifest: dict) -> None:
    """Set context_builder and capture_policy on the runtime from the manifest."""
    section = manifest.get("components") or {}
    if not isinstance(section, dict):
        log.warning("[components] must be a table; ignoring %r", section)
        return

    context = str(section.get("context") or "default")
    if context not in ("default", ""):
        cls = load_symbol(context)
        runtime.context_builder = cls(runtime)
        log.info("context builder: %s", context)

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
