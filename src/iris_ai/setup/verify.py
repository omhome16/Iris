"""One short completion before setup is saved. The key is never included in the text."""

from __future__ import annotations

import asyncio


def verify_model(provider: str, model: str, api_key: str = "") -> tuple[bool, str]:
    """Return whether a one-token call with this key and model succeeded."""
    try:
        return asyncio.run(_verify(provider, model, api_key))
    except Exception as exc:  # noqa: BLE001 - the message is the result
        return False, _plain(exc)


async def _verify(provider: str, model: str, api_key: str) -> tuple[bool, str]:
    import litellm

    from iris_ai.providers import PROVIDERS, qualify

    spec = PROVIDERS.get(provider)
    if spec is None:
        return False, f"unknown provider {provider!r}"
    target = qualify(provider, model) if model else ""
    if not target:
        from iris_ai.config import settings

        target = (getattr(settings, spec.strong_field, "") or "").strip()
    if not target:
        return False, "no model id is set"
    kwargs: dict = {
        "model": target,
        "messages": [{"role": "user", "content": "Reply with the single word ok."}],
        "max_tokens": 4,
        "timeout": 20,
    }
    key = api_key.strip()
    if not key and spec.key_field:
        from iris_ai.config import settings

        key = str(getattr(settings, spec.key_field, "") or "")
    if key:
        kwargs["api_key"] = key
    if spec.base_url:
        kwargs["api_base"] = spec.base_url
    elif spec.base_url_field:
        from iris_ai.config import settings

        base = str(getattr(settings, spec.base_url_field, "") or "")
        if base:
            kwargs["api_base"] = base
    try:
        await litellm.acompletion(**kwargs)
    except Exception as exc:  # noqa: BLE001 - a rejected key is a result, not a crash
        return False, _plain(exc)
    return True, f"{target} answered"


def _plain(exc: BaseException) -> str:
    text = str(exc)
    lowered = text.lower()
    if "401" in text or "invalid api key" in lowered or "incorrect api key" in lowered:
        return "The key was rejected. Check it, or pick a different provider."
    if "404" in text or ("model" in lowered and "not found" in lowered):
        return "That model was not found. Pick another id."
    line = text.strip().splitlines()[0] if text.strip() else type(exc).__name__
    return f"{type(exc).__name__}: {line[:160]}"
