"""`iris bench harness` — the scripted lifecycle check."""

from __future__ import annotations

import json

from iris_ai.bench.harness import scripted
from iris_ai.cli.help_theme import console


def run(which: str) -> int:
    if which != "harness":
        console().print("usage: iris bench harness")
        return 2
    report = scripted()
    console().print(json.dumps(report, ensure_ascii=False))
    return 0 if report.get("ok") else 1
