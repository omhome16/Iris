"""JEV client — a thin, degradable adapter over `typesafe-sdk`.

Why an adapter instead of calling the SDK directly: JEV is an *optional*
dependency at runtime. Iris must keep working on free tiers with no TypeSafe
key, so every call site asks this client, gets either typed answers or `None`,
and falls back to the deterministic path. Deleting this package must leave
Iris exactly as it was before.

Source of truth for the API shape: https://docs.typesafe.ai
(pinned against typesafe-sdk 0.7.0 — `AsyncTypeSafeClient.system_one`,
`Noul`/`Choice`/`Score`, `SystemOneResponse.nouls/choices/scores`).

Pricing: Jev is billed on **input** tokens only, $42/Btok = $0.042/Mtok;
output tokens are free (https://docs.typesafe.ai/models).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from iris import turnlog
from iris.config import settings

log = logging.getLogger("iris.jev")

try:  # the SDK is a normal dependency, but importing must never break boot
    from typesafe_sdk import AsyncTypeSafeClient, RetryPolicy

    _SDK_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # noqa: BLE001 - a missing/broken SDK just disables JEV
    AsyncTypeSafeClient = None  # type: ignore[assignment,misc]
    RetryPolicy = None  # type: ignore[assignment,misc]
    _SDK_IMPORT_ERROR = exc


# ── question builders ────────────────────────────────────────────────────
# Plain dicts rather than SDK objects: the wire format is the SDK's documented
# public input (`NoulModel`/`ChoiceModel`/`ScoreModel`), so question
# construction needs no SDK import and tests need no fake SDK.


def noul(instructions: Any, *, true: Any | None = None, false: Any | None = None) -> dict:
    """A yes/no question; the answer is the probability that it is true."""
    criteria = {k: v for k, v in (("true", true), ("false", false)) if v is not None}
    q: dict = {"type": "noul", "instructions": instructions}
    if criteria:
        q["criteria"] = criteria
    return q


def choice(instructions: Any, criteria: Mapping[str, Any | None]) -> dict:
    """One of a defined, unordered set of options."""
    return {"type": "choice", "instructions": instructions, "criteria": dict(criteria)}


def score(instructions: Any, criteria: Sequence[Any]) -> dict:
    """A position along ordered, described levels."""
    return {"type": "score", "instructions": instructions, "criteria": list(criteria)}


@dataclass(frozen=True, slots=True)
class JevAnswers:
    """Normalized answers, independent of the SDK's response model.

    Keeping Iris's own shape means the SDK can be upgraded (or swapped for the
    raw HTTP API) without touching a single call site.
    """

    nouls: dict[str, float] = field(default_factory=dict)
    choices: dict[str, str] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    confidences: dict[str, float] = field(default_factory=dict)
    model: str = ""
    input_tokens: int = 0
    latency_ms: int = 0

    def noul(self, key: str, default: float = 0.0) -> float:
        return float(self.nouls.get(key, default))

    def choice(self, key: str, default: str = "") -> str:
        return str(self.choices.get(key, default))

    def score(self, key: str, default: float = 0.0) -> float:
        return float(self.scores.get(key, default))

    def confidence(self, key: str, default: float = 0.0) -> float:
        return float(self.confidences.get(key, default))


class JevClient:
    """Best-effort async JEV client. `ask()` returns `None` instead of raising."""

    def __init__(self, ledger: Any | None = None, *, api_key: str | None = None, model: str | None = None) -> None:
        self.ledger = ledger
        self._api_key = (api_key if api_key is not None else settings.typesafe_api_key).strip()
        self._model = (model or settings.jev_model).strip() or "jev-latest"
        self._client: Any | None = None
        # One client, many concurrent callers: a single turn can issue a rerank
        # (inside a recall tool), a guard screen (inside web_search) and a
        # capture judgment, and the reflection pass now runs in the background.
        # Without this lock two of them can each build a client and leak one.
        self._lock: asyncio.Lock | None = None
        # Telemetry for operators: a judgment layer nobody can inspect is
        # indistinguishable from one that is failing silently.
        self.requests = 0
        self.failures = 0
        self.last_latency_ms = 0
        self.last_error = ""

    # ── availability ─────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return bool(
            settings.jev_enabled
            and self._api_key
            and AsyncTypeSafeClient is not None
            and not settings.jev_disabled_reason
        )

    def unavailable_reason(self) -> str:
        if not settings.jev_enabled:
            return "jev_enabled is false"
        if not self._api_key:
            return "TYPESAFE_API_KEY is not set"
        if AsyncTypeSafeClient is None:
            return f"typesafe-sdk is not importable ({_SDK_IMPORT_ERROR})"
        return settings.jev_disabled_reason

    async def _ensure(self) -> Any:
        if self._client is not None:
            return self._client
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            # Re-check inside the lock: whoever lost the race gets this client
            # instead of building a second one.
            if self._client is not None:
                return self._client
            assert AsyncTypeSafeClient is not None  # guarded by `enabled`
            client = AsyncTypeSafeClient(
                api_key=self._api_key,
                model=self._model,
                timeout=settings.jev_timeout_seconds,
                retry=RetryPolicy(  # type: ignore[misc]
                    max_retries=1,
                    backoff_initial=0.3,
                    backoff_max=1.5,
                    timeout=settings.jev_timeout_seconds + 5.0,
                ),
            )
            await client.__aenter__()  # mirrors the Telegram MCP client's lifecycle
            self._client = client
            log.info("jev client ready (model=%s)", self._model)
            return client

    def status(self) -> dict[str, Any]:
        """Health of the judgment layer, for `/health` and status consumers."""
        return {
            "enabled": self.enabled,
            "model": self._model,
            "reason": "" if self.enabled else self.unavailable_reason(),
            "requests": self.requests,
            "failures": self.failures,
            "last_latency_ms": self.last_latency_ms,
            "last_error": self.last_error,
        }

    async def close(self) -> None:
        if self._client is not None:
            with contextlib.suppress(Exception):  # shutdown must not raise
                await self._client.__aexit__(None, None, None)
            self._client = None

    async def _reset(self) -> None:
        """Drop a client whose transport may be poisoned after a failure."""
        await self.close()

    # ── the one call ─────────────────────────────────────────────────────
    async def ask(
        self,
        state: Any,
        questions: Mapping[str, dict],
        *,
        timeout: float | None = None,
    ) -> JevAnswers | None:
        """One request, many independent questions. `None` on any failure.

        Never raises: JEV sits in front of decisions the deterministic path can
        still make, so a JEV outage degrades quality, never availability.

        `timeout` is a **per-call latency budget**, and call sites on the reply
        path pass one. Client-level timeouts protect the request; a budget
        protects the turn — a slow judgment layer must cost a bounded number of
        seconds before the deterministic path takes over, or "JEV is optional"
        stops being true the moment its latency is bad. The budget can only be
        shorter than the client timeout, never longer.
        """
        if not questions or not self.enabled:
            return None
        import time

        started = time.monotonic()
        self.requests += 1
        try:
            client = await self._ensure()
            call = client.system_one(state=state, questions=dict(questions))
            response = await (asyncio.wait_for(call, timeout) if timeout else call)
        except TimeoutError:
            self.failures += 1
            self.last_error = f"budget exceeded: {timeout}s"
            log.warning("jev budget of %ss exceeded; using the deterministic path", timeout)
            await self._reset()
            return None
        except Exception as exc:  # noqa: BLE001 - heterogeneous transport/API failures
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"[:240]
            log.warning("jev request failed (%s): %s", type(exc).__name__, exc)
            await self._reset()
            return None

        nouls = {k: float(v.noul) for k, v in getattr(response, "nouls", {}).items()}
        choices = {k: str(v.choice) for k, v in getattr(response, "choices", {}).items()}
        scores = {k: float(v.score) for k, v in getattr(response, "scores", {}).items()}
        confidences: dict[str, float] = {}
        for key, answer in getattr(response, "choices", {}).items():
            confidences[key] = float(getattr(answer, "confidence", 0.0) or 0.0)
        for key, answer in getattr(response, "scores", {}).items():
            confidences[key] = float(getattr(answer, "confidence", 0.0) or 0.0)

        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        model = str(getattr(response, "model", "") or self._model)
        latency_ms = int((time.monotonic() - started) * 1000)
        self.last_latency_ms = latency_ms
        turnlog.mark("jev", latency_ms)

        if self.ledger is not None:
            try:
                self.ledger.record(
                    model=model,
                    tier="jev",
                    prompt_tokens=input_tokens,
                    completion_tokens=0,
                    cached_tokens=0,
                )
            except Exception as exc:  # noqa: BLE001 - accounting must never break a call
                log.warning("jev ledger write failed: %s", exc)

        log.debug("jev ok: model=%s in=%d latency=%dms q=%d", model, input_tokens, latency_ms, len(questions))
        return JevAnswers(
            nouls=nouls,
            choices=choices,
            scores=scores,
            confidences=confidences,
            model=model,
            input_tokens=input_tokens,
            latency_ms=latency_ms,
        )
