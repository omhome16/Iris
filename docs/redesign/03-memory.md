# 03 — Memory

Memory is Iris's differentiator and the part with the most working code. The
redesign changes *where it stores and how it is chosen*, not the algorithms.

## 1. The four tiers (kept)

The 2026 consensus matches what Iris already does — working, episodic, semantic
and procedural memory — plus provenance as a first-class axis:

| Tier | Iris artifact today | Lifetime |
|---|---|---|
| working | the conversation, compacted | a session |
| episodic | `memory/YYYY-MM-DD.md`, dreams | decaying |
| semantic | `MEMORY.md`, `USER.md` (curated) | evergreen |
| procedural | skills (`SKILL.md`) | until revised |
| judgments | recall feedback, importance | signals over the above |

What is preserved exactly: provenance gates (owner/agent/untrusted/system), the
taint rule that only some sources may graduate to curated memory, recall lanes
(default + temporal escalation), decay, MMR, and the dream consolidation cycle.

## 2. The `MemoryBackend` interface

Everything above the store talks to one interface:

```python
class MemoryBackend(Protocol):
    async def index(self, docs: Iterable[Document]) -> None: ...
    async def search(self, query: str, *, lane: Lane, top_k: int,
                     require_origin: set[Origin] | None = None) -> list[Hit]: ...
    async def forget(self, hit: Hit, *, marker: str) -> None: ...
    async def stats(self) -> dict: ...
```

Retrieval policy (decay × importance × MMR, lane selection) stays in the kernel,
above the backend, because it is *policy*, not storage. A backend answers "which
chunks are near this query"; the kernel decides what that means for this turn.

## 3. Backends

| Backend | Embeddings | Ships as | Use |
|---|---|---|---|
| **SQLite + sqlite-vec** | local model or provider API | **default** | laptop, zero services |
| Postgres + pgvector | provider API | optional | large corpora, existing installs |
| In-memory / null | none | always | tests, degraded mode (recall unavailable, loudly) |
| External vector service | provider | plugin | power users |

**Why SQLite becomes the default (D2):** principle 1 — a harness that needs a
database before it will say hello is not a five-minute harness. sqlite-vec is a
single file, needs no server, and is fast enough for a personal corpus. The
Markdown files remain the source of truth; the index is always rebuildable.

## 4. Embeddings without a mandatory key

The local-first rule means a usable embedded path is required. Options, chosen by
config with automatic fallback:

1. a provider embedding API if a key is present (best quality);
2. a bundled small local model (via `fastembed`/ONNX or Ollama if available);
3. **no embeddings** — FTS/BM25-only recall, which still works and is honest
   about its limits (the current degraded-mode contract, promoted to a backend).

This removes today's hard coupling to Gemini embeddings for a working install.

## 5. Contextual chunking and the write path

Kept: contextual chunk headers, content-hash caching, the deterministic prefilter
before any capture judgment, and the rule that capture can only ADD to episodic
memory, never write curated memory directly. Consolidation into semantic memory
still happens only in the dream/consolidation pass.

## 6. Migration

The current `memory/index.py` becomes `capabilities/memory/pgvector.py`
(unchanged SQL); `files.py`/`dreaming.py`/`forgetting.py` move under
`capabilities/memory/` and are called by the kernel rather than owning the graph.
A one-shot `iris migrate` copies an existing pgvector corpus into the new default
backend (or the user keeps Postgres by setting `[memory] backend = "postgres"`).

## 7. Open question

Whether semantic memory should be *one* curated file (`MEMORY.md`) or a small
graph of entities/relations. The current file is beautifully transparent; a graph
is more capable and less legible. Recommendation: keep the file, add an optional
entity index over it later — legibility first.
