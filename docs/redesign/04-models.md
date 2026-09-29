# 04 — Models & gateway

## 1. One interface

```python
class ModelBackend(Protocol):
    async def complete(self, req: CompletionRequest) -> Completion: ...
    async def stream(self, req: CompletionRequest) -> AsyncIterator[Chunk]: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def capabilities(self) -> ModelCapabilities: ...   # tools, vision, json, cache
```

`CompletionRequest` is provider-neutral: messages (with the normalized role set
`system|user|assistant|tool`), tools, temperature-as-hint, max tokens, timeout,
and a `tier` hint (`cheap` / `strong`). The role normalization learned the hard
way — a stray `human` reached four providers as one failure — is enforced at this
boundary, and every backend must pass the message-contract conformance test.

## 2. The default backend: LiteLLM, wrapped

LiteLLM stays the default implementation because it reaches ~100 providers for
free, but it is now *behind* the interface:

- the provider registry (already shipped in `providers.py`) is the model catalog:
  name, key var, prefix, base URL, verification date;
- failover, retry/jitter, `Retry-After`, prompt caching and the cost ledger live
  in the wrapper, so a second backend gets them for free only if it reuses the
  wrapper — a native SDK backend implements its own or composes the wrapper.

**Rejected:** rewriting the gateway from scratch. LiteLLM's breadth is real; the
problem was never LiteLLM, it was Iris leaking provider-shaped messages into it.

## 3. Native backends (the point of the interface)

Anthropic, OpenAI and Gemini native SDKs offer features LiteLLM abstracts away —
prompt caching controls, extended thinking blocks, computer-use, batch APIs. A
native `ModelBackend` lets a user opt into those per provider while keeping the
same kernel. This is why the interface exists even though LiteLLM covers the
common case.

## 4. Tiers, selection and cost

- Two logical tiers, `cheap` and `strong`, with per-role overrides. A role (e.g.
  the critic) can be pinned to the other tier to break self-preference bias —
  already the case today.
- Sampling quirks are per model, not global: Gemini 3+ omits `temperature`
  (shipped fix); reasoning models get no temperature and a different max-tokens
  meaning.
- Budgets are enforced in the kernel (per-turn, per-day, split by
  input/output/cached/embedding/tool-schema). The gateway records usage; it does
  not decide limits.
- The cost ledger keeps the current shape (every call, estimated cost, cache-hit
  rate) and is exposed through the API and CLI.

## 5. Reliability

Kept: retry with exponential backoff + full jitter, never retry 4xx except 429,
`Retry-After` parsing, four timeout clocks (connect / first-token / stream-idle /
total), and mid-stream failures that flush what was buffered rather than losing a
partial reply. New: a **model health probe** on boot (a cheap no-op call) so a
misconfigured model id fails at startup with an actionable error instead of mid-turn
across four providers.

## 6. What this replaces

| Today | Redesign |
|---|---|
| `memory/llm.py` (concrete LiteLLM client) | `capabilities/models/litellm.py` behind `ModelBackend` |
| `providers.py` table | unchanged — becomes the model catalog |
| implicit "strong/cheap" everywhere | explicit `tier` on the request |
| `_to_provider_messages` boundary | the interface's contract + conformance test |
