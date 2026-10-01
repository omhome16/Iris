"""Setup wizard, catalog, roles, and the plug-and-play seams."""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.agents.roles import roles_from_manifest
from iris_ai.cli.doctor import run_checks
from iris_ai.cli.scaffold import write_new
from iris_ai.cli.setup import apply_wizard
from iris_ai.components import OPTIONS
from iris_ai.config import settings
from iris_ai.mcp.catalog import load_catalog


def test_apply_wizard_stores_the_key_and_does_not_print_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
):
    secret = "sk-test-secret-value"
    monkeypatch.setattr(settings, "harness_config", str(tmp_path / "harness.toml"))
    apply_wizard(
        tmp_path / "workspace",
        provider="openai",
        api_key=secret,
        model="openai/gpt-4o-mini",
        env_path=tmp_path / ".env",
        manifest_path=tmp_path / "harness.toml",
    )
    stored = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"OPENAI_API_KEY={secret}" in stored
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    manifest = (tmp_path / "harness.toml").read_text(encoding="utf-8")
    assert 'llm_provider = "openai"' in manifest
    assert 'context = "default"' in manifest


def test_catalog_presets():
    names = set(load_catalog())
    assert {
        "filesystem",
        "fetch",
        "github",
        "git",
        "brave-search",
        "playwright",
        "sqlite",
    } <= names


def test_roles_from_manifest_keep_the_builtins_and_add_one():
    roles = roles_from_manifest(
        {
            "roles": {
                "scribe": {
                    "prompt": "Write a short brief.",
                    "tools": ["memory_search"],
                    "model_tier": "cheap",
                    "max_steps": 2,
                }
            }
        }
    )
    assert "researcher" in roles
    assert "critic" in roles
    assert roles["scribe"].max_tool_rounds == 2
    assert roles["scribe"].system_prompt.startswith("Write")


def test_new_memory_skeleton_implements_the_backend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(settings, "harness_config", str(tmp_path / "harness.toml"))
    path = write_new("memory", "jsonmem", tmp_path / "examples")
    source = path.read_text(encoding="utf-8")
    for name in (
        "connect",
        "close",
        "search",
        "escalate",
        "stats",
        "upsert_chunks",
        "delete_file_chunks",
        "replace_file_chunks",
        "forget_entry",
    ):
        assert f"async def {name}" in source


def test_each_seam_has_at_least_two_options():
    for kind in ("context", "memory", "persona", "channel"):
        assert len(OPTIONS[kind]) >= 2


async def test_markdown_memory_finds_a_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    (tmp_path / "notes.md").write_text("The spare key is under the blue bowl.\n", encoding="utf-8")
    from iris_ai.memory.markdown_index import MarkdownIndex

    hits = await MarkdownIndex().search("blue bowl")
    assert hits
    assert "blue bowl" in hits[0].content


def test_doctor_names_a_fix_for_every_problem(tmp_path: Path):
    checks = run_checks(env_dir=tmp_path, environ={})
    problems = [check for check in checks if check.level != "ok"]
    assert problems
    assert all(check.fix for check in problems)
