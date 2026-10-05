"""Tools declared as component folders under `components/tool/<name>/`.

Each one runs in the component host. The call waits for the same approval
gate as the other control tools. A folder that fails to declare is skipped.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

from iris_ai.agent.tools import Tool
from iris_ai.plug import components_root
from iris_ai.toolpolicy import EXTERNAL_TOOLS, ExternalTool, Policy, PolicyError, ToolClass, declare_external


def _meta(folder: Path) -> dict[str, Any]:
    path = folder / "component.toml"
    if not path.is_file():
        return {}
    loaded = tomllib.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


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
