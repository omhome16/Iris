"""Agent Plugins 1.0, the closed plugin.json, and Iris's extension directory.

Unknown namespaces are ignored. Iris components travel under
`dev.iris.harness/components/<kind>/<name>/`.
"""

from __future__ import annotations

import json
from pathlib import Path

_ALLOWED = {
    "$schema",
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
    "extensions",
}

NAMESPACE = "dev.iris.harness"


def load_plugin(path: Path) -> dict:
    """Validate plugin.json. Unknown top-level fields are reported, not fatal."""
    manifest = Path(path)
    if manifest.is_dir():
        manifest = manifest / "plugin.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("plugin.json must be an object")
    for key in ("name", "version"):
        if not str(data.get(key) or "").strip():
            raise ValueError(f"plugin.json requires {key}")
    unknown = sorted(set(data) - _ALLOWED)
    skills = sorted((manifest.parent / "skills").glob("*/SKILL.md")) if (manifest.parent / "skills").is_dir() else []
    extension = manifest.parent / NAMESPACE / "components"
    return {
        "name": data["name"],
        "version": data["version"],
        "unknown": unknown,
        "skills": [str(path) for path in skills],
        "components": str(extension) if extension.is_dir() else "",
    }


def export_component(kind: str, name: str, source: Path, dest: Path) -> Path:
    """Write the smallest plugin that carries one Iris component."""
    root = Path(dest)
    folder = root / NAMESPACE / "components" / kind / name
    folder.mkdir(parents=True, exist_ok=True)
    if source.is_file():
        (folder / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    manifest = {
        "name": f"iris-{kind}-{name}",
        "version": "0.1.0",
        "description": f"Iris {kind} component {name}.",
        "license": "Apache-2.0",
        "extensions": {NAMESPACE: {"components": [f"{kind}/{name}"]}},
    }
    (root / "plugin.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return root


def install_plugin(source: Path, workspace: Path) -> dict:
    """Copy skills and map mcp.json. Unknown namespaces are left where they are."""
    root = Path(source)
    info = load_plugin(root)
    skills_root = Path(workspace) / "skills"
    copied = []
    for skill in info["skills"]:
        src = Path(skill)
        dest = skills_root / src.parent.name
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "SKILL.md").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        copied.append(src.parent.name)
    mcp_path = root / "mcp.json"
    mapped: list[str] = []
    if mcp_path.is_file():
        incoming = json.loads(mcp_path.read_text(encoding="utf-8"))
        data_dir = Path(workspace) / "plugins" / str(info["name"])
        data_dir.mkdir(parents=True, exist_ok=True)
        root_text = str(root.resolve())
        data_text = str(data_dir.resolve())

        def expand(value):
            if isinstance(value, str):
                expanded = value.replace("${PLUGIN_ROOT}", root_text).replace("${PLUGIN_DATA}", data_text)
                if "${" in expanded:
                    raise ValueError("mcp.json may only expand PLUGIN_ROOT and PLUGIN_DATA")
                return expanded
            if isinstance(value, dict):
                return {key: expand(item) for key, item in value.items()}
            if isinstance(value, list):
                return [expand(item) for item in value]
            return value

        incoming = expand(incoming)
        servers = incoming.get("mcpServers") if isinstance(incoming, dict) else None
        if servers is None and isinstance(incoming, dict):
            servers = incoming
        target = Path(workspace) / ".mcp.json"
        existing = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {"mcpServers": {}}
        existing.setdefault("mcpServers", {})
        for name, spec in (servers or {}).items():
            if isinstance(spec, dict):
                spec = {**spec, "trust": "review"}
            existing["mcpServers"][name] = spec
            mapped.append(str(name))
        target.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    info["copied_skills"] = copied
    info["mcp"] = mapped
    return info
