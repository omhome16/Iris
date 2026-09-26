"""The provider registry — one table for every model backend Iris can call.

Iris reaches models through LiteLLM, which already knows how to talk to a few
dozen services by name. This module records *which* of them Iris exposes, the
env var that carries the key, the model-id prefix LiteLLM expects, and the base
URL for services that have no native LiteLLM provider (OpenCode Zen/Go, vLLM,
LM Studio, anything OpenAI-compatible).

Why a table instead of `if provider == ...` in three places: the client, the
failover chain and `iris doctor` all read the same registry, so a new provider
cannot exist in one and be missing from another. Adding one is an entry here
plus the matching ``Settings`` fields; ``tests/test_packaging.py`` fails if a
setting is added without documenting it in ``.env.example``, and
``tests/test_providers.py`` fails if a registry entry names a field that does
not exist.

Model ids drift (the changelog records two Groq defaults that broke), so:
defaults ship only where they were verified against the provider's own docs,
each carries the date it was checked, and a provider whose ids are not stable
enough to pin ships empty and makes ``doctor`` say so rather than 404 at
runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

# The date the model ids and base URLs below were last checked against each
# provider's own documentation. Treat anything older than a quarter as suspect.
VERIFIED = "2026-09-26"


@dataclass(frozen=True)
class Provider:
    """One entry in the registry.

    ``key_field``/``strong_field``/``cheap_field`` are ``Settings`` attribute
    names rather than values, because the registry is static and ``Settings``
    is per-process. ``key_env`` is the variable a user actually sets, and is
    what ``doctor`` reports.
    """

    name: str  # the value of LLM_PROVIDER
    label: str  # human name, for doctor and error messages
    prefix: str  # LiteLLM model prefix
    key_field: str  # Settings attr holding the API key ("" = keyless)
    strong_field: str  # Settings attr holding the strong model id
    cheap_field: str  # Settings attr holding the cheap model id
    key_env: str = ""  # env var name ("" = keyless)
    base_url: str = ""  # fixed api_base, "" = LiteLLM's default
    base_url_field: str = ""  # Settings attr holding a user-supplied api_base
    # True when the provider ships no default model id, so the user must set
    # one. `doctor` reports these instead of the first call 404ing, and
    # `Settings._provider_usable` refuses to guess.
    requires_model_config: bool = False
    verified: str = ""  # date the defaults were checked
    docs: str = ""


# Insertion order is the failover order used when the resolved provider fails:
# cloud first (best quality), then the OpenAI-compatible gateways, with local
# Ollama deliberately last because it is the always-available floor.
PROVIDERS: dict[str, Provider] = {
    "gemini": Provider(
        name="gemini",
        label="Google Gemini",
        prefix="gemini/",
        key_field="gemini_api_key",
        strong_field="strong_model",
        cheap_field="cheap_model",
        key_env="GEMINI_API_KEY",
        verified=VERIFIED,
        docs="https://aistudio.google.com/apikey",
    ),
    "groq": Provider(
        name="groq",
        label="Groq",
        prefix="groq/",
        key_field="groq_api_key",
        strong_field="groq_strong_model",
        cheap_field="groq_cheap_model",
        key_env="GROQ_API_KEY",
        verified=VERIFIED,
        docs="https://console.groq.com/keys",
    ),
    "openrouter": Provider(
        name="openrouter",
        label="OpenRouter",
        prefix="openrouter/",
        key_field="openrouter_api_key",
        strong_field="openrouter_strong_model",
        cheap_field="openrouter_cheap_model",
        key_env="OPENROUTER_API_KEY",
        verified=VERIFIED,
        docs="https://openrouter.ai/keys",
    ),
    "openai": Provider(
        name="openai",
        label="OpenAI",
        prefix="openai/",
        key_field="openai_api_key",
        strong_field="openai_strong_model",
        cheap_field="openai_cheap_model",
        key_env="OPENAI_API_KEY",
        requires_model_config=True,
        docs="https://platform.openai.com/api-keys",
    ),
    "deepseek": Provider(
        name="deepseek",
        label="DeepSeek",
        prefix="deepseek/",
        key_field="deepseek_api_key",
        strong_field="deepseek_strong_model",
        cheap_field="deepseek_cheap_model",
        key_env="DEEPSEEK_API_KEY",
        requires_model_config=True,
        docs="https://platform.deepseek.com/api_keys",
    ),
    "xai": Provider(
        name="xai",
        label="xAI Grok",
        prefix="xai/",
        key_field="xai_api_key",
        strong_field="xai_strong_model",
        cheap_field="xai_cheap_model",
        key_env="XAI_API_KEY",
        requires_model_config=True,
        docs="https://console.x.ai",
    ),
    "mistral": Provider(
        name="mistral",
        label="Mistral",
        prefix="mistral/",
        key_field="mistral_api_key",
        strong_field="mistral_strong_model",
        cheap_field="mistral_cheap_model",
        key_env="MISTRAL_API_KEY",
        requires_model_config=True,
        docs="https://console.mistral.ai/api-keys",
    ),
    "together": Provider(
        name="together",
        label="Together AI",
        prefix="together_ai/",
        key_field="together_api_key",
        strong_field="together_strong_model",
        cheap_field="together_cheap_model",
        key_env="TOGETHER_API_KEY",
        requires_model_config=True,
        docs="https://api.together.ai/settings/api-keys",
    ),
    "fireworks": Provider(
        name="fireworks",
        label="Fireworks AI",
        prefix="fireworks_ai/",
        key_field="fireworks_api_key",
        strong_field="fireworks_strong_model",
        cheap_field="fireworks_cheap_model",
        key_env="FIREWORKS_API_KEY",
        requires_model_config=True,
        docs="https://fireworks.ai/account/api-keys",
    ),
    "opencode": Provider(
        name="opencode",
        label="OpenCode Zen",
        prefix="openai/",  # OpenAI-compatible gateway, not a native provider
        key_field="opencode_api_key",
        strong_field="opencode_strong_model",
        cheap_field="opencode_cheap_model",
        key_env="OPENCODE_API_KEY",
        base_url="https://opencode.ai/zen/v1",
        verified=VERIFIED,
        docs="https://opencode.ai/docs/zen",
    ),
    "opencode-go": Provider(
        name="opencode-go",
        label="OpenCode Go",
        prefix="openai/",
        # Zen and Go share one key by design; the difference is the endpoint.
        key_field="opencode_api_key",
        strong_field="opencode_go_strong_model",
        cheap_field="opencode_go_cheap_model",
        key_env="OPENCODE_API_KEY",
        base_url="https://opencode.ai/zen/go/v1",
        verified=VERIFIED,
        docs="https://opencode.ai/docs/go",
    ),
    "openai-compatible": Provider(
        name="openai-compatible",
        label="OpenAI-compatible endpoint",
        prefix="openai/",
        key_field="openai_compatible_api_key",
        strong_field="openai_compatible_strong_model",
        cheap_field="openai_compatible_cheap_model",
        key_env="OPENAI_COMPATIBLE_API_KEY",
        base_url_field="openai_compatible_base_url",
        requires_model_config=True,
        docs="https://docs.litellm.ai/docs/providers/openai_compatible",
    ),
    "ollama": Provider(
        name="ollama",
        label="Ollama (local)",
        prefix="ollama/",
        key_field="",
        strong_field="ollama_strong_model",
        cheap_field="ollama_cheap_model",
        base_url_field="ollama_base_url",
        docs="https://ollama.com",
    ),
}

# What `LLM_PROVIDER=auto` picks when several keys are present. Ordered by
# "most likely to be the best free tier first", matching the README's provider
# table; Ollama is the floor when no key at all is configured.
AUTO_ORDER: tuple[str, ...] = (
    "gemini",
    "groq",
    "openrouter",
    "openai",
    "deepseek",
    "xai",
    "mistral",
    "together",
    "fireworks",
    "opencode",
    "opencode-go",
    "openai-compatible",
)

# Providers that reach the network. Ollama is local, so it never needs a key.
KEYED_PROVIDERS: tuple[Provider, ...] = tuple(p for p in PROVIDERS.values() if p.key_env)

# Every env var that can carry a credential for a model provider, for doctor.
KEY_ENV_VARS: tuple[str, ...] = tuple(p.key_env for p in KEYED_PROVIDERS)


def spec(name: str) -> Provider | None:
    """Look up a provider by name, case-insensitively. None = unknown."""
    return PROVIDERS.get(name.strip().lower())
