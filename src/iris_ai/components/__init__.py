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
    async def extract(self, request):
        return []

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

    async def assemble(self, request):
        from iris_ai.sdk.types import ContextBlock, ContextResult

        text, skills = await self.assemble_turn(request.message, session_id=request.session_id)
        return ContextResult(blocks=(ContextBlock(title="", text=text, priority=0),), skills=tuple(skills))


class MinimalContext:
    """No memory and no skills. The smallest prompt that still answers."""

    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        return "Answer in as few tokens as you can. Do not search memory.", []

    async def assemble(self, request):
        from iris_ai.sdk.types import ContextBlock, ContextResult

        text, skills = await self.assemble_turn(request.message, session_id=request.session_id)
        return ContextResult(blocks=(ContextBlock(title="", text=text, priority=0),), skills=tuple(skills))


OPTIONS: dict[str, tuple[str, ...]] = {
    "context": ("default", "recall-first", "minimal", "temporal-rag"),
    "memory": ("sqlite", "markdown", "pgvector", "evidence-memory"),
    "persona": ("blank", "file", "assistant", "coder", "researcher", "tutor", "strict-reviewer"),
    "capture": ("default", "off", "decision-only"),
    "consolidator": ("dreaming", "off", "conflict-resolver"),
    "engine": ("react", "plan-execute"),
    # Registered transports only. Faces (terminal, http) are `iris serve`.
    "channel": ("telegram",),
}

_CONTEXT = {
    "default": None,
    "recall-first": RecallFirst,
    "minimal": MinimalContext,
    "temporal-rag": None,  # filled below, after the catalog import
}


def list_options(kind: str) -> list[str]:
    """Built-in names plus local folders. A dotted path is always allowed too.

    Channel names come from the channel registry. `terminal` and `http` are
    faces (`iris serve`), not transports a component can load.
    """
    from iris_ai.plug import list_local

    if kind == "channel":
        from iris_ai.channels.registry import CHANNELS

        return list(CHANNELS.names())
    names = list(OPTIONS.get(kind, ()))
    for info in list_local(kind):
        if info.name not in names:
            names.append(info.name)
    return names


def _context_builtin(name: str):
    if name == "temporal-rag":
        from iris_ai.catalog.context.temporal_rag import TemporalRag

        return TemporalRag
    return _CONTEXT.get(name)


def _builtin(kind: str, name: str) -> Any:
    if kind == "context":
        return _context_builtin(name)
    if kind == "persona" and name == "strict-reviewer":
        from iris_ai.catalog.persona.strict_reviewer import StrictReviewer

        return StrictReviewer
    if kind == "capture" and name == "decision-only":
        from iris_ai.catalog.capture.decision_only import DecisionOnly

        return DecisionOnly
    if kind == "consolidator" and name == "conflict-resolver":
        from iris_ai.catalog.consolidator.conflict_resolver import ConflictResolver

        return ConflictResolver
    if kind == "engine" and name == "plan-execute":
        from iris_ai.engines.plan_execute import PlanExecute

        return PlanExecute
    return None


def _resolve_class(kind: str, name: str) -> Any:
    """Built-in, local folder, entry point, or dotted path."""
    from iris_ai.components.resolve import resolve

    return resolve(kind, name)


def _install(runtime: Any, kind: str, name: str, *, fallback: Any = None) -> Any:
    """Build one component. A failure logs one line and returns the fallback."""
    from iris_ai.config import settings
    from iris_ai.plug import Guarded, construct

    if settings.component_host == "subprocess" and kind in {"context", "capture", "consolidator"}:
        from iris_ai.artifacts.store import executable_folder

        if executable_folder(kind, name) is not None:
            return _install_hosted(kind, name, fallback=fallback)
    resolved = None
    try:
        resolved = _resolve_class(kind, name)
    except Exception as exc:  # noqa: BLE001 - a bad component must not stop boot
        log.warning("%s %s failed to load (%s). Using the built-in.", kind, name, exc)
        return fallback
    if resolved is None:
        return fallback
    cls, source = resolved
    if source == "local":
        from iris_ai.isolation.policy import execution_refusal

        refused = execution_refusal(kind)
        if refused:
            log.warning("%s", refused)
            return fallback
        blocked = _refuse_drift(kind, name)
        if blocked:
            log.warning("%s", blocked)
            return fallback
        requires = _refuse_requires(kind, name)
        if requires:
            log.warning("%s", requires)
            return fallback
    try:
        inner = construct(cls, runtime, trust="builtin" if source == "builtin" else "local")
    except Exception as exc:  # noqa: BLE001 - construction errors are reported, not fatal
        log.warning("%s %s failed to start (%s). Using the built-in.", kind, name, exc)
        return fallback
    # Local components are the ones Iris or the owner just added. Watch those.
    # A dotted path stays the class the caller constructed.
    if source == "local" and fallback is not None and kind != "persona":
        return Guarded(inner, fallback, kind=kind, name=name)
    return inner


def _install_hosted(kind: str, name: str, *, fallback: Any) -> Any:
    """Run a local folder in the subprocess host. None means there is no folder."""
    from iris_ai.artifacts.store import executable_folder
    from iris_ai.isolation.host import open_component
    from iris_ai.plug import Guarded

    folder = executable_folder(kind, name)
    if folder is None:
        return None
    from iris_ai.isolation.policy import execution_refusal

    refused = execution_refusal(kind)
    if refused:
        log.warning("%s", refused)
        return fallback
    blocked = _refuse_drift(kind, name)
    if blocked:
        log.warning("%s", blocked)
        return fallback
    requires = _refuse_requires(kind, name)
    if requires:
        log.warning("%s", requires)
        return fallback
    try:
        inner = open_component(folder, kind=kind, name=name)
    except Exception as exc:  # noqa: BLE001 - a bad component must not stop boot
        log.warning("%s %s failed to start (%s). Using the built-in.", kind, name, exc)
        return fallback
    if fallback is not None and kind != "persona":
        return Guarded(inner, fallback, kind=kind, name=name)
    return inner


def _refuse_requires(kind: str, name: str) -> str:
    """A component that asks for a different Iris does not load."""
    import tomllib

    from iris_ai import __version__
    from iris_ai.plug import local_folder
    from iris_ai.requires import accepts

    folder = local_folder(kind, name)
    if folder is None:
        return ""
    manifest = folder / "component.toml"
    if not manifest.is_file():
        return ""
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    specs = data.get("requires") or []
    if isinstance(specs, str):
        specs = [specs]
    for spec in specs:
        try:
            ok = accepts(str(spec), __version__)
        except ValueError as exc:
            return f"{kind} {name} has a requirement Iris cannot read ({exc}). Not loaded."
        if not ok:
            return f"{kind} {name} requires {spec}, and this Iris is {__version__}. Not loaded."
    return ""


def _refuse_drift(kind: str, name: str) -> str:
    """A changed folder does not load. An unpinned folder loads with a warning.

    Trust-on-first-use used to pin whatever bytes happened to be on disk the
    first time Iris imported them. Activation and `components use` pin instead.
    """
    from iris_ai.components.lock import drifted, read_lock
    from iris_ai.plug import component_digest, local_folder

    folder = local_folder(kind, name)
    if folder is None:
        return ""
    row = (read_lock().get("kinds") or {}).get(kind) or {}
    if row.get("active") != name:
        return ""
    pinned = str(row.get("digest") or "")
    if not pinned:
        log.warning("%s %s is selected but not pinned. Run `iris components use` to pin it.", kind, name)
        return ""
    # The store is the approved copy. An edit to the working folder does not
    # change what runs, and it is not a reason to drop back to the built-in.
    from iris_ai.artifacts.store import verify

    if verify(pinned):
        return ""
    digest = component_digest(folder)
    if drifted(kind, digest=digest):
        return (
            f"{kind} {name} changed since it was approved ({pinned[:12]}). "
            f"Using the built-in. Review it, then `iris components use {kind} {name}` to re-pin."
        )
    return ""


def attach(runtime: Any, manifest: dict) -> str:
    """Set context_builder and capture_policy on the runtime from the manifest.

    Returns a notice when a selected persona could not load and the built-in
    is used instead. An empty string means nothing fell back.
    """
    section = manifest.get("components") or {}
    if not isinstance(section, dict):
        log.warning("[components] must be a table; ignoring %r", section)
        return ""
    notice = ""
    from iris_ai.pipeline import component_names

    runtime.pipelines = {
        "context": component_names(section, "context", default="default"),
        "capture": component_names(section, "capture", default="default"),
        "persona": component_names(section, "persona", default="file"),
        "consolidator": component_names(section, "consolidator", default="dreaming"),
    }

    context = runtime.pipelines["context"][-1]
    if context not in ("default", ""):
        from iris_ai.agent.context import ContextAssembler

        built = _install(runtime, "context", context, fallback=ContextAssembler(runtime))
        if built is not None:
            runtime.context_builder = built
            log.info("context builder: %s", context)
        else:
            log.warning("context %s is not available. Using the built-in assembler.", context)

    persona_names = runtime.pipelines["persona"]
    persona = persona_names[-1]
    runtime.persona_choice = ",".join(persona_names) if len(persona_names) > 1 else persona
    if _is_custom_persona(persona):
        # `Runtime` is slots-only and the prompt loads the persona from
        # `persona_choice`. Probing here is what stops a broken component from
        # taking down boot: a failure falls back to the file persona and names
        # the recovery command. `iris doctor` still reports the broken selection.
        try:
            _probe_persona(persona)
        except Exception as exc:  # noqa: BLE001 - a persona must not brick boot
            notice = (
                f"persona {persona} failed to load ({type(exc).__name__}: {exc}). "
                "Using the built-in persona for this process. "
                "Recover with: iris components rollback persona"
            )
            log.error(notice)
            runtime.persona_choice = "file"
    log.info("persona: %s", runtime.persona_choice)

    capture = runtime.pipelines["capture"][-1]
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

    consolidator = runtime.pipelines["consolidator"][-1]
    if consolidator == "off":
        runtime.dreams = OffConsolidator()
        log.info("consolidator: off")
    elif consolidator == "conflict-resolver":
        from iris_ai.catalog.consolidator.conflict_resolver import ConflictResolver

        runtime.consolidator_stage = ConflictResolver()
        log.info("consolidator stage: conflict-resolver")
    elif consolidator not in ("dreaming", "default", ""):
        built = _install(runtime, "consolidator", consolidator, fallback=runtime.dreams)
        if built is not None:
            runtime.dreams = built
            log.info("consolidator: %s", consolidator)
    dreams = getattr(runtime, "dreams", None)
    if dreams is not None and not isinstance(dreams, str):
        try:
            dreams.pipeline = list(runtime.pipelines["consolidator"])
            stage = getattr(runtime, "consolidator_stage", None)
            if stage is not None:
                dreams.extra_stage = stage
        except AttributeError:
            pass
    record_identity(runtime, section)
    return notice


def _piece(kind: str, name: str) -> dict[str, str]:
    """Name, source, and digest for one selected component."""
    from iris_ai.artifacts.store import executable_folder
    from iris_ai.plug import component_digest

    folder = executable_folder(kind, name)
    if folder is not None:
        return {"name": name, "source": "local", "digest": component_digest(folder)}
    if ":" in name:
        return {"name": name, "source": "dotted", "digest": ""}
    return {"name": name or "default", "source": "builtin", "digest": ""}


def record_identity(runtime: Any, section: dict | None = None) -> dict[str, dict[str, str]]:
    """What this process is running. The trace and /explain read this."""
    from iris_ai.config import settings

    section = section or {}
    context = str(section.get("context") or "default")
    if context in ("", "default") or getattr(runtime, "context_builder", None) is None:
        context = "default"
    persona = str(getattr(runtime, "persona_choice", "") or section.get("persona") or "file")
    capture = str(section.get("capture") or "default")
    if capture != "off" and getattr(runtime, "capture_policy", None) is None and capture not in ("default", ""):
        capture = "default"
    consolidator = str(section.get("consolidator") or "dreaming")
    identity = {
        "engine": {"name": str(section.get("engine") or "react"), "source": "builtin", "digest": ""},
        "context": _piece("context", context),
        "memory": _piece("memory", str(settings.memory_backend or "sqlite")),
        "persona": _piece("persona", persona),
        "capture": _piece("capture", capture or "default"),
        "consolidator": _piece("consolidator", consolidator or "dreaming"),
        "channel": _piece("channel", str(settings.channels_enabled or "none")),
    }
    runtime.harness_identity = identity
    runtime.engine_name = identity["engine"]["name"]
    return identity


def _looks_local(kind: str, name: str) -> bool:
    from iris_ai.plug import local_folder

    return local_folder(kind, name) is not None


def _is_custom_persona(persona: str) -> bool:
    """A local folder or `pkg:Class`, rather than a built-in persona name."""
    if not persona or persona in {"file", "blank"}:
        return False
    if _looks_local("persona", persona):
        return True
    return ":" in persona


def _probe_persona(persona: str) -> str:
    """Import, construct, and call `text()`. Raises when the component cannot load."""
    from iris_ai.artifacts.store import executable_folder
    from iris_ai.plug import construct, load_class

    folder = executable_folder("persona", persona)
    if folder is not None:
        component = construct(load_class(folder), None)
    elif ":" in persona:
        component = construct(load_symbol(persona), None)
    else:
        raise ValueError(f"persona {persona!r} is not a local folder or a class path")
    text = component.text()
    if not isinstance(text, str):
        raise TypeError("persona text() must return a string")
    return text
