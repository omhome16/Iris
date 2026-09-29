"""Tests for the declarative harness manifest: precedence, channel lists, and
the rule that an unknown key is visible rather than silent."""

from __future__ import annotations

import types

from iris_ai.manifest import apply_manifest, load_manifest


class _Settings:
    """A stand-in for Settings with the fields the loader may touch."""

    def __init__(self):
        self.channels_enabled = "telegram"
        self.channels_disabled = ""
        self.channel_connect_timeout_s = 10.0
        self.tool_max_calls_per_turn = 8
        self.model_backend = "litellm"
        self.model_fields = {
            "channels_enabled": None,
            "channels_disabled": None,
            "channel_connect_timeout_s": None,
            "tool_max_calls_per_turn": None,
            "model_backend": None,
        }


def test_missing_manifest_is_empty(tmp_path):
    assert load_manifest(tmp_path / "nope.toml") == {}


def test_channels_section_maps_lists_to_settings():
    settings = _Settings()
    applied = apply_manifest(
        settings, {"channels": {"enabled": ["telegram", "discord"], "disabled": ["discord"]}}, environ={}
    )
    assert settings.channels_enabled == "telegram,discord"
    assert settings.channels_disabled == "discord"
    assert set(applied) == {"channels_enabled", "channels_disabled"}


def test_environment_wins_over_the_manifest():
    settings = _Settings()
    apply_manifest(settings, {"tool_max_calls_per_turn": 3}, environ={"TOOL_MAX_CALLS_PER_TURN": "5"})
    assert settings.tool_max_calls_per_turn == 8  # untouched: env owns it


def test_a_key_only_dotenv_introduced_does_not_beat_the_manifest(monkeypatch):
    """The bug this closes: `litellm` loads `.env` into `os.environ` at import, so
    a line the sample file shipped (`WORKSPACE_DIR=./workspace`) looked like a real
    environment variable and silently defeated a profile's own workspace. The
    environment is what was there *before* Iris imported anything."""
    import iris_ai
    from iris_ai.manifest import environment_overrides

    monkeypatch.setenv("IRIS_TEST_PRELOADED", "yes")
    monkeypatch.setenv("IRIS_TEST_FROM_DOTENV", "yes")
    monkeypatch.setattr(iris_ai, "PRELOADED_ENV", frozenset({"IRIS_TEST_PRELOADED"}))

    overrides = environment_overrides()
    assert "IRIS_TEST_PRELOADED" in overrides
    assert "IRIS_TEST_FROM_DOTENV" not in overrides

    settings = _Settings()
    monkeypatch.setenv("TOOL_MAX_CALLS_PER_TURN", "5")  # introduced after boot
    monkeypatch.setattr(iris_ai, "PRELOADED_ENV", frozenset())
    assert apply_manifest(settings, {"tool_max_calls_per_turn": 3}) == ["tool_max_calls_per_turn"]
    assert settings.tool_max_calls_per_turn == 3


def test_a_scalar_is_coerced_to_the_setting_type():
    settings = _Settings()
    apply_manifest(settings, {"tool_max_calls_per_turn": "4"}, environ={})
    assert settings.tool_max_calls_per_turn == 4


def test_a_capability_backend_can_be_selected_from_the_manifest():
    """The example file documents `model_backend`; pin that it is applied, not
    silently dropped as an unknown key."""
    settings = _Settings()
    applied = apply_manifest(settings, {"model_backend": "plugin-thing"}, environ={})
    assert settings.model_backend == "plugin-thing"
    assert applied == ["model_backend"]


def test_unknown_keys_are_ignored(caplog):
    settings = _Settings()
    applied = apply_manifest(settings, {"not_a_setting": 1}, environ={})
    assert applied == []


def test_a_real_toml_file_round_trips(tmp_path):
    path = tmp_path / "harness.toml"
    path.write_text('[channels]\nenabled = ["telegram"]\ndisabled = []\n', encoding="utf-8")
    data = load_manifest(path)
    assert data == {"channels": {"enabled": ["telegram"], "disabled": []}}
    assert isinstance(data, dict) and not isinstance(data, types.MappingProxyType)
