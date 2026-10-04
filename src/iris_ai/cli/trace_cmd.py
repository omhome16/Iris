"""`iris trace` — the last turn, explained."""

from __future__ import annotations

import json
from pathlib import Path

from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.explain import explain_latest, load_traces, render_trace


def run(*, session: str = "", last: int = 1, json_output: bool = False) -> int:
    path = Path(settings.workspace_dir) / "config" / "traces.jsonl"
    count = max(1, last)
    rows = load_traces(path, session=session, last=count)
    out = console()
    if json_output:
        out.print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0 if rows else 1
    if count == 1:
        out.print(explain_latest(path, session=session, include_path=True))
        return 0 if rows else 1
    if not rows:
        out.print(explain_latest(path, session=session, include_path=True))
        return 1
    for index, entry in enumerate(rows):
        if index:
            out.print("")
        out.print(render_trace(entry, path=str(path) if index == 0 else ""))
    return 0
