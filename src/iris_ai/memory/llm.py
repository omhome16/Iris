"""Two-tier LLM client.

Iris uses two model tiers (LiteLLM, provider-agnostic):
- strong: conversation, reasoning, skill writing
- cheap:  extraction, scoring, consolidation (the write path — where the
          tokens go, per the 2026 research: >80% of agent time is writes)

Reliability discipline (from the research brief):
- retries with exponential backoff + full jitter, cap, never retry 4xx (except 429)
- four separate timeout clocks (connect / first-token / stream-idle / total)
- cheap model for anything that doesn't need deep reasoning
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections.abc import Sequence
from typing import Any

import litellm

from iris_ai import turnlog
from iris_ai.config import settings

log = logging.getLogger("iris.llm")

try:
    from iris_ai.ledger import CostLedger
except ImportError:  # pragma: no cover - ledger is always present in the package
    CostLedger = None  # type: ignore[assignment,misc]


class LLMError(RuntimeError):
    pass


def _cached_tokens(usage: Any) -> int:
    """Cache-hit tokens, provider-agnostic.

    OpenAI-style providers report `usage.prompt_tokens_details.cached_tokens`;
    Gemini reports `usage.cachedContentTokenCount`.
    """
    if usage is None:
        return 0
    details = getattr(usage, "prompt_tokens_details", None)
    if details is not None:
        return int(getattr(details, "cached_tokens", 0) or 0)
    return int(getattr(usage, "cachedContentTokenCount", 0) or 0)


def _should_retry(exc: Exception) -> bool:
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    text = str(exc).lower()
    return any(k in text for k in ("timeout", "rate limit", "rate_limit", "quota", "overloaded", "connection"))


def _retry_after(exc: Exception) -> float:
    headers = getattr(exc, "response", None) and getattr(exc.response, "headers", None)
    if headers:
        after = headers.get("retry-after") or headers.get("Retry-After")
        if after:
            try:
                return max(0.0, float(after))
            except ValueError:
                pass
    text = str(exc).lower()
    for marker in ("retry in ", "try again in "):
        idx = text.find(marker)
        if idx >= 0:
            tail = text[idx + len(marker) :].split("s")[0].split(".")[0]
            try:
                return max(0.0, float(tail))
            except ValueError:
                pass
    return 0.0


async def _with_retries(fn, *, max_attempts: int = 4, base_delay: float = 1.0, cap: float = 45.0):
    last: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return await fn()
        except Exception as exc:  # noqa: BLE001 - provider failures are heterogeneous
            last = exc
            if not _should_retry(exc) or attempt == max_attempts - 1:
                break
            delay = _retry_after(exc) or min(cap, base_delay * (2**attempt))
            jitter = random.uniform(delay * 0.8, delay * 1.2)
            await asyncio.sleep(jitter)
    raise LLMError(f"LLM call failed after {max_attempts} attempts: {last}") from last


class LLMClient:
    """Two-tier client with a per-tier throttle.

    Gemini free tier allows ~20 strong-model requests/minute; the throttle
    spaces calls so bursts degrade into slight delays instead of 429s.
    """

    _strong_lock = asyncio.Lock()
    _last_strong_call: float = 0.0
    _strong_min_interval: float = 3.5  # ~17 req/min < 20 free-tier cap

    def __init__(self, ledger: CostLedger | None = None) -> None:
        self.strong_model = settings.strong_model
        self.cheap_model = settings.cheap_model
        self.embedding_model = settings.embedding_model
        self.embedding_dim = settings.embedding_dim
        self.ledger = ledger
        self._ensure_cache()

    @staticmethod
    def _ensure_cache() -> None:
        """The per-call `caching=True` flag is a silent no-op unless a
        cache object is actually installed on litellm. Without this, the
        /costs cache-hit readout always read 0% no matter what."""
        if not settings.llm_caching or litellm.cache is not None:
            return
        try:
            litellm.cache = litellm.Cache(type="local")
            log.info("LiteLLM local prompt cache enabled")
        except Exception as exc:  # noqa: BLE001 - caching must never break boot
            log.warning("could not enable the LiteLLM cache: %s", exc)

    def _auth_kwargs(self, model: str) -> dict[str, Any]:
        """Provider credentials from settings, so .env works without shell
        exports.

        Resolved by the provider registry (`Settings.auth_for_model`) rather
        than a chain of `startswith` here: the OpenAI-compatible gateways all
        share the `openai/` prefix and differ only by api_base, so a local
        prefix check would silently send a Zen call to OpenAI's endpoint.
        """
        return dict(settings.auth_for_model(model))

    def _record(self, model: str, tier: str, usage: Any) -> None:
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        # The turn-scoped accumulator comes first, and deliberately before the
        # ledger check: the ledger is the long-term record, this is what makes a
        # turn's own spend visible while it is still happening (the multi-agent
        # path costs ~15x a chat, so it must be priced where the choice was
        # made). A turn with no ledger configured still gets a token count.
        cached = _cached_tokens(usage)
        turnlog.add_usage(
            tier=tier,
            model=model,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
        )
        if self.ledger is None:
            return
        self.ledger.record(
            model=model,
            tier=tier,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=cached,
        )

    @classmethod
    async def _throttle_strong(cls) -> None:
        async with cls._strong_lock:
            wait = cls._last_strong_call + cls._strong_min_interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            cls._last_strong_call = time.monotonic()

    async def _strong_call(self) -> None:
        await self._throttle_strong()

    async def complete(
        self,
        messages: Sequence[dict[str, str]],
        *,
        tier: str = "strong",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        timeout: float = 90.0,
        max_attempts: int = 4,
    ) -> str:
        candidates = settings.llm_candidates(tier)
        last_exc: Exception | None = None
        for idx, (provider, model, auth) in enumerate(candidates):
            async def call(_model=model, _auth=auth):
                if tier == "strong":
                    await self._throttle_strong()
                kwargs: dict[str, Any] = dict(
                    model=_model,
                    messages=list(messages),
                    temperature=temperature,
                    timeout=timeout,
                    caching=settings.llm_caching,
                    **_auth,
                )
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = await litellm.acompletion(**kwargs)
                self._record(_model, tier, getattr(resp, "usage", None))
                return resp.choices[0].message.content

            try:
                return await _with_retries(call, max_attempts=max_attempts)
            except Exception as exc:  # noqa: BLE001 - heterogeneous provider failures
                last_exc = exc
                if idx + 1 < len(candidates):
                    log.warning(
                        "provider %s failed after %d attempts (%s); failing over to %s",
                        provider,
                        max_attempts,
                        exc,
                        candidates[idx + 1][0],
                    )
        raise LLMError(
            f"all providers failed ({[c[0] for c in candidates]}): {last_exc}"
        ) from last_exc

    async def complete_with_tools(
        self,
        messages: Sequence[dict[str, str]],
        tools: list[dict] | None = None,
        *,
        tier: str = "strong",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: float = 90.0,
        max_attempts: int = 4,
    ) -> tuple[str, list[dict], str]:
        """Completion that may emit tool calls.

        Returns (text, tool_calls, thinking) where tool_calls is a list of
        {"name": str, "args": dict} and thinking is any model reasoning
        (`reasoning_content` / `reasoning` on the message, provider-agnostic).
        """
        candidates = settings.llm_candidates(tier)
        last_exc: Exception | None = None
        for idx, (provider, model, auth) in enumerate(candidates):
            async def call(_model=model, _auth=auth):
                if tier == "strong":
                    await self._throttle_strong()
                kwargs: dict[str, Any] = dict(
                    model=_model,
                    messages=list(messages),
                    temperature=temperature,
                    timeout=timeout,
                    caching=settings.llm_caching,
                    **_auth,
                )
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                if tools:
                    kwargs["tools"] = tools
                resp = await litellm.acompletion(**kwargs)
                self._record(_model, tier, getattr(resp, "usage", None))
                message = resp.choices[0].message
                calls: list[dict] = []
                for tc in getattr(message, "tool_calls", None) or []:
                    args = tc.function.arguments or "{}"
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                    calls.append({"name": tc.function.name, "args": args})
                thinking = (
                    getattr(message, "reasoning_content", None)
                    or getattr(message, "reasoning", None)
                    or ""
                )
                return message.content or "", calls, thinking

            try:
                return await _with_retries(call, max_attempts=max_attempts)
            except Exception as exc:  # noqa: BLE001 - heterogeneous provider failures
                last_exc = exc
                if idx + 1 < len(candidates):
                    log.warning(
                        "provider %s failed after %d attempts (%s); failing over to %s",
                        provider,
                        max_attempts,
                        exc,
                        candidates[idx + 1][0],
                    )
        raise LLMError(
            f"all providers failed ({[c[0] for c in candidates]}): {last_exc}"
        ) from last_exc

    async def stream_complete_with_tools(
        self,
        messages: Sequence[dict[str, str]],
        tools: list[dict] | None = None,
        *,
        tier: str = "strong",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: float = 90.0,
        max_attempts: int = 4,
    ):
        """Streamed completion with reasoning + tool-call visibility.

        Yields (kind, payload) tuples:
        - ("thinking", str)   — reasoning delta
        - ("text", str)       — reply text delta
        - ("tool_call", dict) — a complete {"id", "name", "args"} call
        - ("done", str)       — full reasoning text (may be "")

        Retries before the first chunk only; a mid-stream failure surfaces
        whatever was buffered so a partial reply is never lost.
        """
        candidates = settings.llm_candidates(tier)
        last: Exception | None = None
        for idx, (provider, model, auth) in enumerate(candidates):
            started = False
            for attempt in range(max_attempts):
                if tier == "strong":
                    await self._throttle_strong()
                kwargs: dict[str, Any] = dict(
                    model=model,
                    messages=list(messages),
                    temperature=temperature,
                    timeout=timeout,
                    caching=settings.llm_caching,
                    **auth,
                )
                if max_tokens:
                    kwargs["max_tokens"] = max_tokens
                if tools:
                    kwargs["tools"] = tools
                try:
                    resp = await litellm.acompletion(
                        stream=True,
                        stream_options={"include_usage": True},
                        **kwargs,
                    )
                    fragments: dict[int, str] = {}
                    ids: dict[int, str] = {}
                    names: dict[int, str] = {}
                    thinking = ""
                    async for chunk in resp:
                        started = True
                        usage = getattr(chunk, "usage", None)
                        if usage is not None:
                            self._record(model, tier, usage)
                            continue
                        choices = getattr(chunk, "choices", None) or []
                        if not choices:
                            continue
                        delta = getattr(choices[0], "delta", None)
                        if delta is None:
                            continue
                        reasoning = getattr(delta, "reasoning_content", None)
                        if reasoning:
                            thinking += reasoning
                            yield ("thinking", reasoning)
                        content = getattr(delta, "content", None)
                        if content:
                            yield ("text", content)
                        for tc in getattr(delta, "tool_calls", None) or []:
                            idx2 = getattr(tc, "index", 0) or 0
                            if getattr(tc, "id", None):
                                ids[idx2] = tc.id
                            fn = getattr(tc, "function", None)
                            if fn is not None:
                                if getattr(fn, "name", None):
                                    names[idx2] = fn.name
                                args_frag = getattr(fn, "arguments", None)
                                if args_frag:
                                    fragments[idx2] = fragments.get(idx2, "") + args_frag
                    if fragments:
                        for idx2 in sorted(fragments):
                            try:
                                args = json.loads(fragments[idx2] or "{}")
                            except json.JSONDecodeError:
                                args = {}
                            yield (
                                "tool_call",
                                {"id": ids.get(idx2, ""), "name": names.get(idx2, ""), "args": args},
                            )
                    yield ("done", thinking)
                    return
                except Exception as exc:  # noqa: BLE001 - provider failures are heterogeneous
                    last = exc
                    if started or not _should_retry(exc) or attempt == max_attempts - 1:
                        break
                    delay = _retry_after(exc) or min(45.0, 1.0 * (2**attempt))
                    await asyncio.sleep(random.uniform(delay * 0.8, delay * 1.2))
            if started:
                yield ("done", "")  # mid-stream failure: flush what we buffered
                return
            if idx + 1 < len(candidates):
                log.warning(
                    "provider %s failed after %d attempts (%s); failing over to %s",
                    provider,
                    max_attempts,
                    last,
                    candidates[idx + 1][0],
                )
                continue
            raise LLMError(
                f"all providers failed ({[c[0] for c in candidates]}): {last}"
            ) from last

    async def embed(self, texts: Sequence[str], *, timeout: float = 60.0) -> list[list[float]]:
        async def call():
            kwargs: dict[str, Any] = dict(
                model=self.embedding_model,
                input=list(texts),
                timeout=timeout,
                **self._auth_kwargs(self.embedding_model),
            )
            if self.embedding_model.startswith("gemini/"):
                kwargs["dimensions"] = self.embedding_dim
            resp = await litellm.aembedding(**kwargs)
            self._record(self.embedding_model, "embedding", getattr(resp, "usage", None))
            return [item["embedding"] for item in resp.data]

        return await _with_retries(call)

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]
