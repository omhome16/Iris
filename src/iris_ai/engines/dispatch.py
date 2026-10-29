"""Which engine decisions ChatGraph should make.

The model call stays in ChatGraph. This module owns the difference between
react and plan-execute so those branches are not string compares at each site.
`PlanExecute.plan` is not the turn: that class does not call the model the
way the chat graph does.
"""

from __future__ import annotations

import contextlib
from typing import Any, Protocol

from iris_ai.registry import Registry, RegistryError


class TurnEngine(Protocol):
    name: str

    def short_circuit(self, state: dict) -> str | None: ...

    def wants_plan(self, state: dict) -> bool: ...

    def streams(self) -> bool: ...

    def tool_phase(self) -> dict: ...

    def finish(self, graph: Any, done: dict) -> None: ...

    def after_tools(self, state: dict) -> None: ...


class _React:
    name = "react"

    def short_circuit(self, state: dict) -> str | None:
        return None

    def wants_plan(self, state: dict) -> bool:
        return False

    def streams(self) -> bool:
        return True

    def tool_phase(self) -> dict:
        return {}

    def finish(self, graph: Any, done: dict) -> None:
        return None

    def after_tools(self, state: dict) -> None:
        return None


class _Plan:
    name = "plan-execute"

    def short_circuit(self, state: dict) -> str | None:
        if state.get("engine_phase") == "verify":
            return "verify"
        return None

    def wants_plan(self, state: dict) -> bool:
        return not state.get("engine_planned")

    def streams(self) -> bool:
        return False

    def tool_phase(self) -> dict:
        return {"engine_phase": "act"}

    def finish(self, graph: Any, done: dict) -> None:
        graph.engine_path.append("verify")
        done["engine_phase"] = "done"
        done["engine_event"] = "plan-execute: verify"

    def after_tools(self, state: dict) -> None:
        if state.get("engine_phase") == "act":
            state["engine_phase"] = "verify"


#: Built-in turn engines. A folder, an entry point, or a dotted class joins this
#: list. An unknown name fails here; it does not silently become react.
ENGINES: Registry[TurnEngine] = Registry("engine")
ENGINES.register("react", lambda **_kwargs: _React(), source="core")
ENGINES.register("plan-execute", lambda **_kwargs: _Plan(), source="core")


def known_engines() -> list[str]:
    """Registry names plus local engine folders."""
    names = set(ENGINES.names())
    from iris_ai.plug import list_local

    for info in list_local("engine"):
        names.add(info.name)
    return sorted(names)


def _unknown(name: str) -> RegistryError:
    known = ", ".join(known_engines()) or "none"
    return RegistryError(f"unknown engine {name!r} — registered: {known}")


def resolve(name: str | None) -> TurnEngine:
    """The engine config named, or react when nothing is configured.

    `None` and `""` are the unconfigured case. `react` is the explicit default.
    Any other unknown name, including a dotted path that does not import, raises
    with the list of engines that do exist.
    """
    if name is None or str(name).strip() == "":
        return _React()
    text = str(name).strip()
    if text == "react":
        return _React()
    if text == "plan-execute":
        return _Plan()
    from iris_ai.components.resolve import resolve as resolve_component
    from iris_ai.plug import construct

    try:
        found = resolve_component("engine", text)
    except Exception as exc:
        raise _unknown(text) from exc
    if found is not None:
        cls, source = found
        if source != "builtin":
            try:
                engine = construct(cls, None, trust="local")
            except Exception as exc:
                raise _unknown(text) from exc
            if not callable(getattr(engine, "streams", None)):
                raise _unknown(text)
            if not getattr(engine, "name", ""):
                with contextlib.suppress(AttributeError, TypeError):
                    engine.name = text
            return engine
    if text in ENGINES:
        return ENGINES.build(text)
    raise _unknown(text)
