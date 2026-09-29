"""Model backend capability — the `ModelBackend` Protocol and its registry.

The kernel asks a model to complete (optionally with tools), to stream, and to
embed. It never imports LiteLLM, a provider SDK, or a transport: it holds a
`ModelBackend`. The core implementation is `LLMClient` (LiteLLM), registered as
``litellm``; a native SDK backend or a self-hosted gateway registers the same way.

The Protocol also owns the **message contract**. Provider roles are
``system|user|assistant|tool``; a LangChain role (``human``/``ai``) must never
cross this boundary. That rule is enforced in the adapter and asserted by a
conformance test, because the one time it was not, the critic failed on four
providers at once.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from iris_ai.memory.llm import LLMClient
from iris_ai.registry import Registry

#: Method names the Protocol requires. Exported so the conformance test asserts
#: the same list it documents, rather than a copy that can drift.
REQUIRED: tuple[str, ...] = (
    "complete",
    "complete_with_tools",
    "stream_complete_with_tools",
    "embed",
    "embed_one",
)


@runtime_checkable
class ModelBackend(Protocol):
    """Everything the kernel may ask of a model provider."""

    async def complete(
        self,
        messages: list[dict],
        *,
        tier: str = "strong",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        json_mode: bool = False,
        timeout: float = 90.0,
        max_attempts: int = 4,
    ) -> str: ...

    async def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        *,
        tier: str = "strong",
        temperature: float = 0.2,
        max_tokens: int | None = None,
        timeout: float = 90.0,
        max_attempts: int = 4,
    ) -> tuple[str, list[dict], str]: ...

    def stream_complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> Any: ...

    async def embed(self, texts: list[str], *, timeout: float = 60.0) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]: ...


#: The model registry. Core owns ``litellm``; discovery adds installed backends.
MODELS: Registry[ModelBackend] = Registry("model_backend")

MODELS.register("litellm", lambda **kw: LLMClient(**kw), source="core")


def discover_models(enabled: set[str] | None = None) -> list[str]:
    """Register installed model backends; returns the names that were added."""
    return [reg.name for reg in MODELS.discover("iris_ai.models", enabled=enabled)]
