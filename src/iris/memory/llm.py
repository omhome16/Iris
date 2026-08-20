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
import random
import time
from typing import Any, Sequence

import litellm

from iris.config import settings

try:
    from iris.ledger import CostLedger
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

    def _auth_kwargs(self, model: str) -> dict[str, Any]:
        """Provider credentials from settings (so .env works without shell
        exports). LiteLLM names: api_key for cloud providers, api_base for
        Ollama."""
        if model.startswith("gemini/"):
            return {"api_key": settings.gemini_api_key} if settings.gemini_api_key else {}
        if model.startswith("groq/"):
            return {"api_key": settings.groq_api_key} if settings.groq_api_key else {}
        if model.startswith("openrouter/"):
            return {"api_key": settings.openrouter_api_key} if settings.openrouter_api_key else {}
        if model.startswith("ollama/"):
            return {"api_base": settings.ollama_base_url}
        return {}

    def _record(self, model: str, tier: str, usage: Any) -> None:
        if self.ledger is None:
            return
        prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
        completion = int(getattr(usage, "completion_tokens", 0) or 0)
        self.ledger.record(
            model=model,
            tier=tier,
            prompt_tokens=prompt,
            completion_tokens=completion,
            cached_tokens=_cached_tokens(usage),
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
        model = self.strong_model if tier == "strong" else self.cheap_model

        async def call():
            if tier == "strong":
                await self._throttle_strong()
            kwargs: dict[str, Any] = dict(
                model=model,
                messages=list(messages),
                temperature=temperature,
                timeout=timeout,
                caching=settings.llm_caching,
                **self._auth_kwargs(model),
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await litellm.acompletion(**kwargs)
            self._record(model, tier, getattr(resp, "usage", None))
            return resp.choices[0].message.content

        return await _with_retries(call, max_attempts=max_attempts)

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
        model = self.strong_model if tier == "strong" else self.cheap_model

        async def call():
            if tier == "strong":
                await self._throttle_strong()
            kwargs: dict[str, Any] = dict(
                model=model,
                messages=list(messages),
                temperature=temperature,
                timeout=timeout,
                caching=settings.llm_caching,
                **self._auth_kwargs(model),
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if tools:
                kwargs["tools"] = tools
            resp = await litellm.acompletion(**kwargs)
            self._record(model, tier, getattr(resp, "usage", None))
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

        return await _with_retries(call, max_attempts=max_attempts)

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
        model = self.strong_model if tier == "strong" else self.cheap_model
        last: Exception | None = None
        for attempt in range(max_attempts):
            if tier == "strong":
                await self._throttle_strong()
            kwargs: dict[str, Any] = dict(
                model=model,
                messages=list(messages),
                temperature=temperature,
                timeout=timeout,
                caching=settings.llm_caching,
                **self._auth_kwargs(model),
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if tools:
                kwargs["tools"] = tools
            started = False
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
                        idx = getattr(tc, "index", 0) or 0
                        if getattr(tc, "id", None):
                            ids[idx] = tc.id
                        fn = getattr(tc, "function", None)
                        if fn is not None:
                            if getattr(fn, "name", None):
                                names[idx] = fn.name
                            args_frag = getattr(fn, "arguments", None)
                            if args_frag:
                                fragments[idx] = fragments.get(idx, "") + args_frag
                if fragments:
                    for idx in sorted(fragments):
                        try:
                            args = json.loads(fragments[idx] or "{}")
                        except json.JSONDecodeError:
                            args = {}
                        yield (
                            "tool_call",
                            {"id": ids.get(idx, ""), "name": names.get(idx, ""), "args": args},
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
        else:
            raise LLMError(f"LLM call failed after {max_attempts} attempts: {last}") from last

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