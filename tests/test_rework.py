"""Setup reload, model prefixes, time zones, and local components."""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.cli.ask import ScriptedPrompter
from iris_ai.cli.tui.statusbar import render_status
from iris_ai.config import Settings
from iris_ai.plug import activate, check_folder, scaffold, staging_dir
from iris_ai.providers import qualify
from iris_ai.setup.flow import collect
from iris_ai.timeutil import try_zone, zone


def test_qualify_adds_a_prefix_once():
    assert qualify("groq", "openai/gpt-oss-120b") == "groq/openai/gpt-oss-120b"
    assert qualify("groq", "groq/openai/gpt-oss-120b") == "groq/openai/gpt-oss-120b"


def test_status_bar_does_not_double_the_provider():
    text = render_status(
        name="Biyoo",
        provider="groq",
        model="groq/openai/gpt-oss-120b",
        session="cli",
    )
    assert text.startswith("Biyoo   groq · openai/gpt-oss-120b")
    assert "groq/groq/" not in text


def test_utc_zone_does_not_need_the_system_database():
    assert zone("UTC") is not None
    assert zone("Not/AZone") is not None
    assert try_zone("Not/AZone") is None


def test_empty_embedding_model_stays_empty():
    fresh = Settings(embedding_model="", gemini_api_key="")
    assert fresh.embedding_model == ""


def test_manifest_model_is_resolved_after_load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    manifest = tmp_path / "harness.toml"
    manifest.write_text(
        'llm_provider = "groq"\ngroq_strong_model = "openai/gpt-oss-120b"\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("GROQ_API_KEY=sk-test\n", encoding="utf-8")
    from iris_ai.manifest import apply_manifest, load_manifest

    targeted = Settings(harness_config=str(manifest), groq_api_key="sk-test")
    apply_manifest(targeted, load_manifest(manifest))
    assert targeted._resolved_provider == "groq"
    assert targeted._models_strong["groq"] == "groq/openai/gpt-oss-120b"


def test_scripted_setup_plan_does_not_need_a_terminal():
    plan = collect(
        ScriptedPrompter(
            {
                "Provider": "groq",
                "GROQ_API_KEY": "sk-test",
                "Model": "other",
                "Model id": "openai/gpt-oss-120b",
                "Memory": "none",
                "Your name": "Ada",
                "Assistant name": "Iris",
                "Timezone": "UTC",
                "Tone": "plain",
                "Extras": [],
                "Write this setup?": True,
            }
        ),
        skip_verify=True,
    )
    assert plan is not None
    assert plan.provider == "groq"
    assert plan.model == "groq/openai/gpt-oss-120b"
    assert plan.embeddings == "none"
    assert "sk-test" not in " ".join(plan.assistant_name)


def test_local_component_imports_by_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    folder = scaffold("context", "tasks-first", root=tmp_path / "components")
    ok, detail = check_folder(folder)
    assert ok, detail


def test_staging_rejects_a_path_escape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    folder = staging_dir("context", "safe-name")
    assert folder.is_relative_to((tmp_path / "components" / ".staging").resolve())
    with pytest.raises(ValueError):
        staging_dir("context", "../escape")


def test_activate_requires_a_staged_component(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    harness = tmp_path / "config"
    harness.mkdir()
    (harness / "harness.toml").write_text('llm_provider = "auto"\n', encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(harness / "harness.toml"))
    staged = staging_dir("context", "tasks-first")
    staged.mkdir(parents=True)
    source = scaffold("context", "tasks-first", root=tmp_path / "elsewhere")
    for item in source.iterdir():
        (staged / item.name).write_text(item.read_text(encoding="utf-8"), encoding="utf-8")
    message = activate("context", "tasks-first")
    assert "activated" in message
    assert (tmp_path / "components" / "context" / "tasks-first" / "component.py").is_file()
