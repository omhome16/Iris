"""`iris_ai.api._staged_preview` — staged dream signals for the /mind payload."""

from __future__ import annotations

import json
from pathlib import Path

from iris_ai.api import _staged_preview
from iris_ai.memory.files import WorkspaceFiles


def test_staged_preview_reads_staging_jsonl(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    stage = files.staging_dir() / "staging-2026-09-22.jsonl"
    stage.write_text(
        "\n".join(
            [
                json.dumps({"content": "owner prefers dark mode", "importance": 7.5, "target": "MEMORY.md"}),
                "{not json",
                json.dumps({"content": "", "importance": 1}),
                json.dumps({"content": "renews lease in March", "importance": 6}),
            ]
        ),
        encoding="utf-8",
    )

    out = _staged_preview(files)

    assert [s["content"] for s in out] == [
        "owner prefers dark mode",
        "renews lease in March",
    ]
    assert out[0]["importance"] == 7.5


def test_staged_preview_empty_when_no_staging(tmp_path: Path):
    files = WorkspaceFiles(tmp_path)
    assert _staged_preview(files) == []
