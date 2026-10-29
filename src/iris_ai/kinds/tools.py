"""Tools declared as component folders under `components/tool/<name>/`.

Each one runs in the component host. The call waits for the same approval
gate as the other control tools. A folder that fails to declare is skipped.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Any

from iris_ai.agent.tools import Tool
from iris_ai.plug import components_root
from iris_ai.toolpolicy import EXTERNAL_TOOLS, ExternalTool, Policy, PolicyError, ToolClass, declare_external

log = logging.getLogger("iris.kinds.tools")


def _meta(folder: Path) -> dict[str, Any]:
    path = folder / "component.toml"
    if not path.is_file():
        return {}
    loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def approved_tool_folder(name: str, folder: Path) -> Path | None:
    """The pinned store copy, or None when the tool is not pinned or the pin does not verify.

    A tool runs with the owner's approval, so an unpinned folder is not loaded.
    The lock keeps one digest per tool name under ``pins`` because several tool
    folders can be approved at once, unlike the single active component of the
    other kinds.
    """
    from iris_ai.artifacts.store import artifact_dir, verify
    from iris_ai.components.lock import read_lock
    from iris_ai.plug import component_digest

    row = (read_lock().get("kinds") or {}).get("tool") or {}
    pins = row.get("pins") if isinstance(row.get("pins"), dict) else {}
    pinned = str(pins.get(name) or "")
    if not pinned and row.get("active") == name:
        pinned = str(row.get("digest") or "")
    if not pinned:
        log.warning("tool %s is not pinned; not loaded", name)
        return None
    if not verify(pinned):
        log.warning("tool %s pin %s does not verify; not loaded", name, pinned[:12])
        return None
    if component_digest(folder) != pinned:
        log.warning("tool %s working folder changed; running the pinned copy", name)
    return artifact_dir(pinned)


def load_kind_tools() -> list[Tool]:
    root = components_root() / "tool"
    if not root.is_dir():
        return []
    from iris_ai.isolation.policy import execution_refusal

    refusal = execution_refusal("tool")
    tools: list[Tool] = []
    for folder in sorted(path for path in root.iterdir() if path.is_dir()):
        if not (folder / "component.py").is_file():
            continue
        meta = _meta(folder)
        name = str(meta.get("name") or folder.name)
        approved = approved_tool_folder(name, folder)
        if approved is None:
            continue
        folder = approved
        meta = _meta(folder)
        name = str(meta.get("name") or folder.name)
        if not name.isidentifier():
            continue
        description = str(meta.get("description") or name)
        parameters = meta.get("parameters")
        if not isinstance(parameters, dict):
            parameters = {
                "type": "object",
                "properties": {"message": {"type": "string"}},
                "required": [],
            }
        if name not in EXTERNAL_TOOLS:
            try:
                declare_external(
                    name,
                    ExternalTool(
                        ToolClass.CONTROL,
                        Policy.ASK,
                        "a tool component runs only after approval",
                        "component",
                        "extended",
                    ),
                )
            except PolicyError:
                continue

        async def handler(folder: Path = folder, name: str = name, **kwargs: Any) -> str:
            if refusal:
                return json.dumps({"ok": False, "error": refusal})
            from iris_ai.agent.tools import _err, _honour_approval, approval_payload
            from iris_ai.isolation.host import open_component

            shown = {
                "tool": name,
                "changes": f"run tool component {name}",
                **approval_payload("kind_tool", {"name": name, **kwargs}),
            }
            decision = _honour_approval(shown)
            if decision != "approved":
                return _err(decision)
            hosted = open_component(folder, kind="tool", name=name)
            try:
                import asyncio

                payload = await asyncio.to_thread(hosted.call, "run", kwargs)
            finally:
                hosted.close()
            if isinstance(payload, str):
                return payload
            return json.dumps(payload if payload is not None else {"ok": True}, ensure_ascii=False)

        tools.append(Tool(name, description, parameters, handler))
    return tools
