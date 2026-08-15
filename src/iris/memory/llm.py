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
import random
import time
from typing import Any, Sequence

import litellm

from iris.config import settings


class LLMError(RuntimeError):
    pass


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

    def __init__(self) -> None:
        self.strong_model = settings.strong_model
        self.cheap_model = settings.cheap_model
        self.embedding_model = settings.embedding_model
        self.embedding_dim = settings.embedding_dim

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
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if json_mode:
                kwargs["response_format"] = {"type": "json_object"}
            resp = await litellm.acompletion(**kwargs)
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
    ) -> tuple[str, list[dict]]:
        """Completion that may emit tool calls.

        Returns (text, tool_calls) where tool_calls is a list of
        {"name": str, "args": dict}.
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
            )
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            if tools:
                kwargs["tools"] = tools
            resp = await litellm.acompletion(**kwargs)
            message = resp.choices[0].message
            calls: list[dict] = []
            for tc in getattr(message, "tool_calls", None) or []:
                args = tc.function.arguments or "{}"
                import json

                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
                calls.append({"name": tc.function.name, "args": args})
            return message.content or "", calls

        return await _with_retries(call, max_attempts=max_attempts)

    async def embed(self, texts: Sequence[str], *, timeout: float = 60.0) -> list[list[float]]:
        async def call():
            kwargs: dict[str, Any] = dict(model=self.embedding_model, input=list(texts), timeout=timeout)
            if self.embedding_model.startswith("gemini/"):
                kwargs["dimensions"] = self.embedding_dim
            resp = await litellm.aembedding(**kwargs)
            return [item["embedding"] for item in resp.data]

        return await _with_retries(call)

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]