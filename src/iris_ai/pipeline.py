"""How a kind stacks. Memory, engine, and channel are one choice, not a pipeline."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

PIPELINE_KINDS = ("context", "capture", "persona", "consolidator")


def parse_names(value: Any) -> list[str]:
    """A name, a comma list, or a list of names."""
    if value is None:
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value)]


def selection(section: dict, kind: str, *, default: str) -> list[str]:
    """The pipeline for one kind, from the flat key or the `[components.<kind>]` table."""
    table = section.get(kind)
    if isinstance(table, dict):
        if table.get("pipeline"):
            return parse_names(table.get("pipeline"))
        if table.get("extends"):
            return [*parse_names(table.get("extends")), *parse_names(table.get("name") or kind)]
    raw = section.get(kind)
    if isinstance(raw, str) and raw:
        return parse_names(raw)
    return [default]


def expand_extends(names: list[str], extends_of: dict[str, str]) -> list[str]:
    """A component that says `extends = default` becomes that chain. Cycles refuse."""
    out: list[str] = []
    seen: set[str] = set()

    def walk(name: str, stack: tuple[str, ...]) -> None:
        if name in stack:
            raise ValueError(f"pipeline cycle: {' -> '.join((*stack, name))}")
        parent = extends_of.get(name, "")
        if parent and parent not in seen:
            walk(parent, (*stack, name))
        if name not in seen:
            seen.add(name)
            out.append(name)

    for name in names:
        walk(name, ())
    return out


def trim_blocks(blocks: list, *, max_chars: int) -> list:
    """Drop the highest priority number until the rendered prefix fits."""
    kept = list(blocks)
    while kept and sum(len(getattr(block, "text", "") or "") for block in kept) > max_chars:
        index = max(range(len(kept)), key=lambda i: getattr(kept[i], "priority", 0))
        kept.pop(index)
    return kept


def redact_candidates(candidates: list, *, scrubbed: list[str] | None = None) -> list:
    """Drop a candidate whose text redaction would change."""
    from iris_ai.redact import redact_text

    kept = []
    for candidate in candidates:
        text = str(getattr(candidate, "content", candidate))
        if redact_text(text) != text:
            if scrubbed is not None:
                scrubbed.append(text)
            continue
        kept.append(candidate)
    return kept


def component_names(section: dict, kind: str, *, default: str) -> list[str]:
    """Names for one kind, including a `[components.<kind>]` pipeline table."""
    raw = section.get(kind, default)
    if isinstance(raw, dict):
        return selection(section, kind, default=default)
    if isinstance(raw, str) and raw:
        return parse_names(raw)
    return [default]


def record_stage(stages: list[dict], kind: str, name: str) -> None:
    """One pipeline stage, with source and digest when a local folder exists."""
    from iris_ai.plug import component_digest, local_folder

    folder = local_folder(kind, name)
    stages.append(
        {
            "name": name,
            "source": "local" if folder is not None else "builtin",
            "digest": component_digest(folder) if folder is not None else "",
        }
    )


async def fold_context(runtime: Any, names: list[str], message: str, state: dict) -> tuple[str, tuple[str, ...]]:
    """Run each context stage. `budget` trims. `redact` drops a block redaction would change."""
    from iris_ai import turnlog
    from iris_ai.agent.context import ContextAssembler
    from iris_ai.components import _builtin
    from iris_ai.plug import construct
    from iris_ai.sdk.types import ContextRequest, ContextResult

    request = ContextRequest(
        message=message,
        session_id=str(state.get("session_id") or ""),
        origin=str(state.get("origin") or "owner"),
    )
    blocks: list = []
    skills: list[str] = []
    stages: list[dict] = []
    fails = getattr(runtime, "pipeline_fails", None)
    if not isinstance(fails, dict):
        fails = {}
        runtime.pipeline_fails = fails
    for name in names:
        if name in {"budget", "redact"}:
            record_stage(stages, "context", name)
            continue
        if int(fails.get(f"context:{name}") or 0) >= 3:
            continue
        try:
            if name in {"default", ""}:
                result = await ContextAssembler(runtime).assemble(request)
            else:
                cls = _builtin("context", name)
                component = construct(cls, runtime, trust="builtin") if cls is not None else None
                if component is None or not hasattr(component, "assemble"):
                    continue
                result = await component.assemble(request)
            blocks.extend(list(getattr(result, "blocks", ()) or ()))
            skills.extend(getattr(result, "skills", ()) or ())
            record_stage(stages, "context", name)
        except Exception:  # noqa: BLE001 - one stage must not stop the rest of the prefix
            fails[f"context:{name}"] = int(fails.get(f"context:{name}") or 0) + 1
    if "redact" in names:
        from iris_ai.redact import redact_text

        blocks = [block for block in blocks if redact_text(str(getattr(block, "text", ""))) == str(getattr(block, "text", ""))]
    if "budget" in names:
        blocks = trim_blocks(blocks, max_chars=4000)
    turnlog.record("pipeline", pipeline="context", stages=stages)
    return ContextResult(blocks=tuple(blocks)).render(), tuple(dict.fromkeys(skills))
