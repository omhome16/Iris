"""Small edits to harness.toml. Secrets never go in this file."""

from __future__ import annotations

from pathlib import Path


def _quote(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_quote(item) for item in value) + "]"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{text}"'


def upsert(path: Path, key: str, value: object, *, table: str = "") -> None:
    """Set one key, at the top of the file or inside `[table]`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f"{key} = {_quote(value)}"
    if not table:
        text = _replace_top(text, key, line)
    else:
        text = _replace_in_table(text, table, key, line)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _replace_top(text: str, key: str, line: str) -> str:
    lines = text.splitlines()
    out: list[str] = []
    replaced = False
    in_table = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_table = True
        if (not in_table and stripped.startswith(key + " ")) or (
            not in_table and stripped.startswith(key + "=")
        ):
            if not replaced:
                out.append(line)
                replaced = True
            continue
        out.append(raw)
    if not replaced:
        out.insert(0, line)
    return "\n".join(out) + "\n"


def _replace_in_table(text: str, table: str, key: str, line: str) -> str:
    header = f"[{table}]"
    lines = text.splitlines()
    if header not in {raw.strip() for raw in lines}:
        extra = text.rstrip() + f"\n\n{header}\n{line}\n"
        return extra
    out: list[str] = []
    in_section = False
    replaced = False
    for raw in lines:
        stripped = raw.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if in_section and not replaced:
                out.append(line)
                replaced = True
            in_section = stripped == header
        if in_section and (stripped.startswith(key + " ") or stripped.startswith(key + "=")):
            if not replaced:
                out.append(line)
                replaced = True
            continue
        out.append(raw)
    if in_section and not replaced:
        out.append(line)
    return "\n".join(out) + "\n"
