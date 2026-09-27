"""The provider registry: one table, and everything that must agree with it.

The registry exists so the client, the failover chain and `iris doctor` cannot
drift apart. These tests hold that line from three directions:

* the table itself is internally consistent (every field it names exists on
  ``Settings``, every key env var matches its field, no duplicate names);
* resolution behaves — an unusable provider is skipped rather than attempted,
  an unknown name degrades instead of crashing, and the OpenAI-compatible
  gateways (which all share the `openai/` prefix) resolve to their own base URL;
* `doctor` sees the new keys, because an invisible credential is the exact bug
  this replaced (only three key names were checked before).
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.cli.doctor import run_checks
from iris_ai.config import Settings
from iris_ai.providers import AUTO_ORDER, KEYED_PROVIDERS, PROVIDERS, spec

ZEN_BASE = "https://opencode.ai/zen/v1"
GO_BASE = "https://opencode.ai/zen/go/v1"


def _settings(**overrides) -> Settings:
    """A Settings isolated from the ambient environment for provider tests.

    Explicit kwargs win over env in pydantic-settings, so every provider the
    assertions touch is pinned here; anything left alone is not asserted on.
    """
    base = {
        "llm_provider": "auto",
        "gemini_api_key": "",
        "groq_api_key": "",
        "openrouter_api_key": "",
        "openai_api_key": "",
        "deepseek_api_key": "",
        "xai_api_key": "",
        "mistral_api_key": "",
        "together_api_key": "",
        "fireworks_api_key": "",
        "opencode_api_key": "",
        "openai_compatible_api_key": "",
        "openai_compatible_base_url": "",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


# ── the table is internally consistent ───────────────────────────────────


def test_every_registry_field_names_a_real_setting():
    """A typo in the registry would otherwise be a silent AttributeError path."""
    fields = set(Settings.model_fields)
    for name, provider in PROVIDERS.items():
        assert provider.name == name, f"{name}: name field disagrees with its key"
        for field in (provider.key_field, provider.strong_field, provider.cheap_field):
            # An empty field is meaningful: `key_field=""` marks a keyless
            # provider (Ollama), so only non-empty names have to exist.
            assert not field or field in fields, f"{name}: unknown Settings field {field!r}"
        if provider.base_url_field:
            assert provider.base_url_field in fields, f"{name}: unknown base URL field"
        assert provider.prefix.endswith("/"), f"{name}: prefix must end in /"


def test_key_env_matches_its_settings_field():
    """`iris doctor` maps an env var straight to a setting, so they must line up.

    OpenCode Go deliberately shares Zen's key, which is why the check is on the
    field's own name rather than uniqueness across the table.
    """
    for provider in KEYED_PROVIDERS:
        assert provider.key_env == provider.key_field.upper(), provider.name


def test_auto_order_lists_real_providers_and_leaves_ollama_as_the_floor():
    for name in AUTO_ORDER:
        assert name in PROVIDERS, name
    # Ollama is local and keyless: it is the fallback, never the auto pick.
    assert "ollama" not in AUTO_ORDER
    assert spec("ollama") is not None


def test_unknown_provider_name_is_not_in_the_table():
    assert spec("not-a-provider") is None


# ── resolution behaves ───────────────────────────────────────────────────


def test_zen_resolves_to_the_openai_prefix_with_its_own_base_url():
    settings = _settings(llm_provider="opencode", opencode_api_key="zen-key")
    assert settings._resolved_provider == "opencode"
    provider, model, auth = settings.llm_candidates("strong")[0]
    assert provider == "opencode"
    assert model.startswith("openai/")
    assert auth == {"api_key": "zen-key", "api_base": ZEN_BASE}


def test_go_shares_the_key_but_not_the_endpoint():
    settings = _settings(llm_provider="opencode-go", opencode_api_key="go-key")
    provider, model, auth = settings.llm_candidates("strong")[0]
    assert provider == "opencode-go"
    assert auth["api_base"] == GO_BASE
    assert auth["api_key"] == "go-key"
    assert model.startswith("openai/")


def test_a_configured_but_model_less_provider_is_skipped_not_attempted():
    """A key without a model id must not become a call with a guessed model."""
    settings = _settings(llm_provider="openai", openai_api_key="sk-x")
    assert settings._resolved_provider != "openai"
    assert "not usable" in settings.provider_warning
    assert "openai" not in [name for name, _model, _auth in settings.llm_candidates("strong")]


def test_an_unknown_provider_degrades_instead_of_crashing():
    settings = _settings(llm_provider="gpt-five", opencode_api_key="k")
    assert "unknown LLM_PROVIDER" in settings.provider_warning
    assert settings._resolved_provider in PROVIDERS


def test_openai_compatible_needs_a_base_url():
    no_url = _settings(
        llm_provider="openai-compatible",
        openai_compatible_api_key="k",
        openai_compatible_strong_model="openai/local-model",
        openai_compatible_cheap_model="openai/local-model",
    )
    assert no_url._resolved_provider != "openai-compatible"

    with_url = _settings(
        llm_provider="openai-compatible",
        openai_compatible_api_key="k",
        openai_compatible_base_url="http://localhost:8000/v1",
        openai_compatible_strong_model="openai/local-model",
        openai_compatible_cheap_model="openai/local-model",
    )
    assert with_url._resolved_provider == "openai-compatible"
    _provider, _model, auth = with_url.llm_candidates("strong")[0]
    assert auth["api_base"] == "http://localhost:8000/v1"


def test_the_configured_provider_leads_and_ollama_is_last():
    settings = _settings(
        llm_provider="opencode",
        opencode_api_key="zen-key",
        groq_api_key="groq-key",
    )
    names = [name for name, _model, _auth in settings.llm_candidates("strong")]
    assert names[0] == "opencode"
    assert names[-1] == "ollama"
    assert "groq" in names  # a second configured provider is available to fail over to


def test_a_keyless_install_still_resolves_to_ollama():
    settings = _settings()
    assert settings._resolved_provider == "ollama"
    assert settings.llm_candidates("strong")


def test_auth_for_model_disambiguates_the_shared_openai_prefix():
    """Zen, Go and the bring-your-own endpoint all speak `openai/`; the model id
    is the only thing that says which base URL to use."""
    settings = _settings(
        llm_provider="opencode",
        opencode_api_key="zen-key",
        openai_api_key="sk-openai",
        openai_strong_model="openai/gpt-x",
        openai_cheap_model="openai/gpt-x-mini",
    )
    assert settings.auth_for_model("openai/deepseek-v4-pro")["api_base"] == ZEN_BASE
    assert "api_base" not in settings.auth_for_model("openai/gpt-x")


def test_embeddings_still_resolve_their_auth_through_the_registry():
    settings = _settings(gemini_api_key="g-key")
    assert settings.auth_for_model("gemini/gemini-embedding-001") == {"api_key": "g-key"}
    keyless = _settings()
    assert keyless.auth_for_model("ollama/nomic-embed-text") == {"api_base": keyless.ollama_base_url}


# ── doctor reads the same table ──────────────────────────────────────────


def test_doctor_sees_a_zen_key_and_names_the_provider(tmp_path: Path):
    checks = run_checks(
        env_dir=tmp_path,
        environ={"OPENCODE_API_KEY": "zen-key", "LLM_PROVIDER": "opencode"},
    )
    by_name = {c.name: c for c in checks}
    assert by_name["provider keys"].level == "ok"
    assert "OPENCODE_API_KEY" in by_name["provider keys"].detail
    assert by_name["model provider"].level == "ok"
    assert "OpenCode Zen" in by_name["model provider"].detail


def test_doctor_warns_when_a_named_provider_has_no_model_id(tmp_path: Path):
    """The OpenAI provider ships no default model id on purpose, so doctor must
    say so rather than let the first turn 404."""
    checks = run_checks(
        env_dir=tmp_path,
        environ={"OPENAI_API_KEY": "sk-x", "LLM_PROVIDER": "openai"},
    )
    details = {c.name: c.detail for c in checks}
    assert "OPENAI_STRONG_MODEL" in details["strong model"]


def test_doctor_flags_an_unknown_provider_name(tmp_path: Path):
    checks = run_checks(env_dir=tmp_path, environ={"LLM_PROVIDER": "gpt-five"})
    levels = {c.name: c.level for c in checks}
    assert levels["LLM_PROVIDER"] == "warn"
