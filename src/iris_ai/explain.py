"""Why a turn looked the way it did.

Pure rendering of one trace record. The CLI, the plain chat, and the
full-screen chat all call this, so the words stay the same.
"""

from __future__ import annotations

import json
from pathlib import Path

_KINDS = ("engine", "context", "memory", "persona", "capture", "consolidator", "channel")


def render_trace(entry: dict, *, path: str = "") -> str:
    """One turn, as text. ASCII, so a Windows console can print it."""
    lines = ["Harness explanation", ""]
    lines.append("Model")
    lines.append(f"  {entry.get('model') or '(not recorded)'}")
    if entry.get("engine") or entry.get("nodes"):
        lines.append("")
        lines.append("Engine")
        path = " > ".join(entry.get("nodes") or [])
        lines.append(f"  {entry.get('engine') or 'react'}" + (f"  {path}" if path else ""))
    lines.append("")
    lines.append("Components")
    harness = entry.get("harness") or {}
    if not isinstance(harness, dict) or not harness:
        lines.append("  (not recorded)")
    else:
        for kind in _KINDS:
            piece = harness.get(kind) or {}
            if not isinstance(piece, dict) or not piece.get("name"):
                continue
            digest = str(piece.get("digest") or "")[:12]
            suffix = f"  {digest}" if digest else ""
            lines.append(f"  {kind:<14} {piece.get('name')}  {piece.get('source', '')}{suffix}")
    lines.extend(_pipeline_lines(entry))
    lines.append("")
    lines.append("Context")
    lines.append(f"  {int(entry.get('context_chars') or 0)} characters")
    lines.append("")
    lines.append("Tools")
    tools = entry.get("tools") or []
    if not tools:
        lines.append("  (none)")
    else:
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            lines.append(
                f"  {tool.get('name', '')}  {tool.get('policy_class', '')}  {tool.get('decision', '')}".rstrip()
            )
    lines.append("")
    lines.append("Tokens")
    lines.append(f"  {int(entry.get('tokens') or 0)}")
    lines.append("")
    lines.append("Cost")
    lines.append(f"  ${float(entry.get('cost_usd') or 0):.6f}")
    if path:
        lines.append("")
        lines.append("Trace")
        lines.append(f"  {path}")
    return "\n".join(lines)


def _pipeline_lines(entry: dict) -> list[str]:
    """Stages that ran, with the selected pipeline when a stage left no event."""
    shown: dict[str, list[str]] = {}
    pipelines = entry.get("pipelines") or {}
    if isinstance(pipelines, dict):
        for kind in ("context", "capture", "persona", "consolidator"):
            names = _stage_names(pipelines.get(kind))
            if names:
                shown[kind] = names
    for event in entry.get("events") or []:
        if not isinstance(event, dict) or event.get("kind") != "pipeline":
            continue
        kind = str(event.get("pipeline") or "")
        names = _stage_names(event.get("stages"))
        if kind and names:
            shown[kind] = names
    if not shown:
        return []
    lines = ["", "Pipelines"]
    for kind in ("context", "capture", "persona", "consolidator"):
        names = shown.get(kind)
        if names:
            lines.append(f"  {kind:<14} {', '.join(names)}")
    return lines


def _stage_names(raw: object) -> list[str]:
    if isinstance(raw, str):
        return [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for stage in raw:
        if isinstance(stage, dict):
            name = str(stage.get("name") or "").strip()
        else:
            name = str(stage or "").strip()
        if name:
            names.append(name)
    return names


def load_traces(path: Path, *, session: str = "", last: int = 1) -> list[dict]:
    """Newest matching traces first. A missing file is an empty list."""
    if not path.is_file():
        return []
    found: list[dict] = []
    for line in reversed(path.read_text(encoding="utf-8").splitlines()):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if session and str(entry.get("session_id") or "") != session:
            continue
        found.append(entry)
        if len(found) >= max(1, last):
            break
    return found


def explain_latest(path: Path, *, session: str = "", include_path: bool = False) -> str:
    """The newest turn, or a line that says none were recorded."""
    rows = load_traces(path, session=session, last=1)
    shown = str(path) if include_path else ""
    if not rows:
        text = "No turns recorded yet."
        if shown:
            text += f"\n\nTrace\n  {shown}"
        return text
    return render_trace(rows[0], path=shown)
