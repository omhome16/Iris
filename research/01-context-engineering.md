# Context Engineering — Research Report (2024–2026)

**Prepared:** August 2026 · **Purpose:** inform a production AI engineering portfolio project
**Scope:** the discipline of managing what enters an LLM's context window — techniques, reported numbers, decision framework, and production implications.

---

## 1. Techniques: How They Work + Reported Numbers

### 1.1 Context Management (Windowing, Summarization, Eviction, Context Rot)

**Core finding — context rot:** Chroma Research (July 2025) tested 18 frontier models across 194,480 LLM calls and found *every* model degrades as input length grows, well before the window fills. Key results:

- Performance drops at every length increment on tasks as simple as fact retrieval and text replication — degradation is driven by **raw length, distractors, low needle–question similarity, and haystack structure**, not just task difficulty.
- Distractors (semantically similar but wrong passages) have **non-uniform impact**; even one distractor degrades accuracy, four compound it.
- Low semantic overlap between query and answer accelerates decay steeply (tested across 5 embedding models).
- Llama 4 Maverick claims 10M tokens but scored 28.1% at 128K on real-world-style tasks. Practical rule of thumb: **effective context ≈ 30–60% of advertised window** (~150K–400K of a 2M window for high accuracy).

**NoLiMa (Adobe, ICML 2025):** needle-in-a-haystack without literal lexical overlap. Of 13 models claiming ≥128K context, **11 drop below 50% of their short-context baseline at 32K tokens**; GPT-4o fell 99.3% → 69.7%. Effective length (≥85% of baseline) was typically ≤2–8K tokens.

**RULER (NVIDIA):** despite near-perfect vanilla needle-in-a-haystack, only ~half of models claiming ≥32K can effectively handle 32K on harder tasks.

**Lost in the Middle (Liu et al., Stanford):** U-shaped performance curve — models best use info at the start/end of context; GPT-3.5-Turbo multi-doc QA dropped >20% in the worst mid-context position, sometimes below closed-book performance.

**"Context Length Alone Hurts" (EMNLP 2025):** even with *perfect retrieval*, masked distractors, and evidence placed adjacent to the question, performance degrades 13.9%–85% as input grows to 30K. Mitigation — *recite evidence then solve* (retrieve → recite → re-prompt with only the recited evidence): up to +4% on RULER for GPT-4o.

**Eviction policies (inference-side):** the KV cache grows with context; fixed-budget policies evict tokens:
- **StreamingLLM (ICLR 2024):** sliding window + "attention sinks" (keep first ~4 tokens' KV). Stable perplexity over 4M+ tokens; **up to 22.2× speedup** vs sliding-window-with-recomputation.
- **H2O / TOVA / SnapKV / KeyDiff:** attention-weighted or similarity-based eviction. KeyDiff (2025): <0.04% accuracy drop at 23% KV-cache reduction (8K budget, LongBench, Llama 3.1-8B); attention-based eviction degrades badly under block-wise prompt processing.
- vLLM added experimental mid-session KV eviction with sink preservation (2026).

**Summarization / compaction (the production standard):**
- **Anthropic compaction** (`compact_20260112`): summarize the transcript near the limit, restart with the summary. Default trigger ~150K tokens; costs an inference pass; lossy by design — tune the summary prompt for recall first, then precision.
- **Tool-result clearing** (`clear_tool_uses_20250919`): surgically replaces stale `tool_result` blocks with placeholders (keeps the `tool_use` record); default trigger 100K, keeps 3 tool uses. Reported: **+29% agentic-search performance alone; +39% combined with the memory tool; 84% token reduction in a 100-turn web-search eval** (Anthropic, Sept 2025).
- **Memory tool** (`memory_20250818`): file-based structured note-taking outside the window; persists across sessions.
- **Claude Code budget anatomy:** 200K window ≈ 8K system prompt + 5–10K tool schemas + 16K output reserve ≈ 30K fixed overhead, leaving ~170K for history; 13K-token safety margin; compaction triggers before overflow. History is "the primary target of the compaction system."

**Sliding windows:** fixed-size rolling history of the most recent N turns. Cheap and latency-friendly but lossy (StreamingLLM shows naive windowing collapses when initial tokens are evicted — hence sinks). OpenAI's Realtime API uses truncation with `retention_ratio` (e.g., 0.7 = keep 70%, drop oldest 30% in one event) to trade context preservation vs cache hits.

---

### 1.2 Contextual Retrieval (Anthropic, Sept 2024)

**How it works:** before embedding/BM25-indexing, prepend a chunk-specific, LLM-generated context snippet (typically 50–100 tokens) that situates the chunk within its source document ("This chunk covers X from a doc about Y, in section Z…"). Two sub-techniques:

- **Contextual Embeddings** — semantic vectors of contextualized chunks
- **Contextual BM25** — lexical index of contextualized chunks (merged with semantic results via reciprocal-rank fusion)

**Reported gains (top-20-chunk retrieval failure rate):**

| Technique | Failure rate | Improvement |
|---|---|---|
| Baseline | 5.7% | — |
| + Contextual Embeddings | 3.7% | **−35%** |
| + Contextual Embeddings + BM25 | 2.9% | **−49%** |
| + reranking (Cohere) | 1.9% | **−67%** |

Cookbook replication (codebase corpus, 248 queries): Pass@10 went **87.15% → 92.34% (contextual embeddings) → 93.21% (+hybrid BM25) → 95.26% (+reranking)** — a 47% reduction in retrieval failures vs baseline. Contextual embeddings delivered the largest single jump (+5–7 pp).

**What didn't work:** generic document summaries on chunks, HyDE (hypothetical document embeddings), summary-based indexing — all showed limited/low gains.

**Cost/latency notes:**
- Contextualization is a **one-time ingestion cost** (not per-query like HyDE).
- **Prompt caching makes it practical:** 737-chunk ingestion saw 61.83% of input tokens read from cache; ~$9.20 → $2.85 (69% savings); ~$1.02 per million document tokens for 800-token chunks in 8K-token docs (70–80% of input from cache).
- Reranking adds **~100–200 ms/query and ~$0.002/query**.
- Related work: Cornell's Contextual Document Embeddings (CDE) — top MTEB scores for its size class, strongest on small domain-specific (finance/medical) datasets.

---

### 1.3 Prompt Caching / KV Caching (Anthropic, OpenAI, Gemini)

**Mechanism:** the prefill computation (KV tensors) of a stable prompt prefix is stored server-side; a byte-identical prefix on a later request is read from cache instead of recomputed. Caches exact prefixes only — any drift (even whitespace, JSON key order) invalidates everything after the change point. Caches hold KV tensors, not raw text (no privacy regression vs normal processing).

#### Anthropic (explicit, `cache_control`)

| Operation | Multiplier vs base input | Notes |
|---|---|---|
| Cache write — 5-min TTL (default) | 1.25× | 25% premium |
| Cache write — 1-hour TTL | 2.0× | 100% premium |
| Cache read (hit / refresh) | 0.10× | **90% discount**, same for both tiers |

- Up to **4 cache breakpoints** per request; min cacheable prefix **1,024–4,096 tokens** (model-dependent). Breakpoints themselves are free — you only pay for content actually written/read.
- TTLs: 5-minute default (sliding, refreshed free on each hit); 1-hour opt-in (`ttl: "1h"`). **1-hour blocks must precede 5-minute blocks** in the prefix. TTL measured from request *start*.
- Example (Sonnet 4.6): base $3/M → 5m write $3.75, 1h write $6, read $0.30. Opus 4.7: $5 → $6.25 / $10 / $0.50.
- **Break-even math (the discipline):** 5-min tier breaks even at ~2 reads (1.4–3 depending on source); 1-hour tier at ~3 reads (2.2–11). Average cost on cached portion at N hits (5-min): N=2 → 0.675×, N=5 → 0.33×, N→∞ → 0.1×. **Caching with <~3 reuses per TTL window is a net loss.**
- Real-world scale: 200K-token Opus session — cold write after idle gap $1.25; warm in-window message $0.10. A 100-turn Opus coding session: $50–100 input uncached vs ~$10–19 at 90% hit rate (this economics is why Claude Code Pro is viable).
- **2026 regression:** Anthropic silently dropped Claude Code's default cache TTL from 1h to 5m (March 2026) — idle gaps ≥5 min now force cold writes, raising per-session costs 30–60% for bursty workloads. Best practice: **pin an explicit TTL on every breakpoint**.
- Cache hits are **not deducted from rate limits** (Anthropic) — another reason to cache.

#### OpenAI (automatic, then explicit)

- Automatic since Oct 2024 for ≥1,024-token prompts; prefix matched in 128-token increments; org-scoped; caches cleared after 5–10 min inactivity, always <1h.
- Discounts: **gpt-4o 50%** ($2.50 → $1.25); gpt-4.1 75% ($2 → $0.50); **newer models 90%** (gpt-5.2 $1.75 → $0.175; gpt-5.6 family $5 → $0.50 cached, writes 1.25×).
- GPT-5.6+ adds explicit breakpoints, `prompt_cache_key` routing, and a 30-minute exact TTL; **extended caching up to 24h** via KV offload to GPU-local storage (default on GPT-5.5+; `in_memory` unsupported there).
- `prompt_cache_key` raised one customer's hit rate **60% → 87%**. Cookbook claims up to **80% TTFT latency reduction** on long prompts (>10K tokens) and up to 90% input-cost reduction.
- Gotcha: cached tokens still count toward TPM rate limits; concurrent cold-start stampede — 100 parallel requests all paying write premium while the first write materializes (2–4 s).

#### Gemini (implicit + explicit)

- **Implicit caching:** on by default for Gemini 2.5+ (min 2,048–4,096 tokens depending on model); best-effort savings, no guarantee. Put large/common content at the *beginning*.
- **Explicit caching:** create a cache object with a TTL (default 1 hour, no min/max) → cached tokens billed at **~0.1× input rate plus hourly storage** (e.g., Gemini 3 Flash: $0.30 input, $0.03 cached, $1.00/M tokens/hour storage). Best for ≥32K-token contexts reused over hours. Guaranteed savings but requires managing cache lifecycle.

**Best practices (all providers):**
- Cache system prompt + tool definitions + static document/RAG context (in that order — tools sit at the top of the prefix). Push per-user/volatile content *after* the stable block.
- Treat **cache hit rate as a tier-1 production metric** (log `cache_read_input_tokens` vs `cache_creation_input_tokens`; investigate below ~70%, expect >90%).
- Two-level caching: cache the stable system-only prefix even when the outer system+retrieved-context prefix misses.
- Re-warm before TTL expiry for low-traffic endpoints; don't cache single-use prefixes (pure write-premium loss).
- Deterministic serialization (stable JSON key order, stable tool order) or you'll never hit cache.

---

### 1.4 Context Compression Techniques (production status)

| Technique | Approach | Reported numbers | Production status |
|---|---|---|---|
| **LLMLingua** (MSR 2023) | Small LM (GPT2-small/LLaMA-7B) removes low-information-entropy tokens | up to 20× compression, minimal loss | Open-source, usable; niche |
| **LongLLMLingua** (ACL 2024) | Adds question-aware + coarse-to-fine selection; mitigates lost-in-the-middle | **+21.4% RAG performance with 1/4 of tokens** | Same |
| **LLMLingua-2** (ACL 2024 Findings) | Token *classification* (preserve/discard) with BERT-level encoder trained via GPT-4 data distillation; bidirectional context; extractive (faithful — no new words) | 3–6× faster compression; **1.6–2.9× end-to-end latency acceleration at 2–5× compression**; SOTA out-of-domain (LongBench, GSM8K, BBH) | Same — strongest of the family |
| **ICAE** (ICLR 2024, MSR) | LoRA-adapted encoder compresses context into k "memory slots" the untuned LLM conditions on | **4× compression, ~1% extra params, >2× inference speedup**; 2,048 slots ≈ 4,096-token context, ~20GB GPU memory saved | Research; V2 Mistral-7B weights released |
| **AutoCompressor** (EMNLP 2023) | Recursive compression into summary vectors (soft prompts); unsupervised on up to 30,720-token sequences | Summary vectors substitute for in-context demos, improving accuracy while cutting cost | Research |
| **GIST** | Fine-tuned gist tokens | — | Research, superseded by ICAE |

**Verdict:** prompt *compression* (token deletion / learned slots) remains primarily a research niche. The production-standard "compression" is **summarization/compaction by an LLM** (Anthropic's approach), or **retrieval** (don't put it in context at all). LLMLingua-2 is the most production-ready of the deletion-based family (small, fast, model-agnostic, faithfulness-guaranteed).

---

### 1.5 Long Context vs Retrieval vs Memory (the 2025–26 debate)

**The debate timeline:** 2024 — "context windows are solved, RAG is dead" (Gemini 1.5 Pro 99.7% single-fact NIAH at 1M). Mid-2025 — context-rot research rehabilitated retrieval: realistic multi-fact recall dropped to ~60%, effective windows 30–60% of claimed, million-token latency 30–60× a tuned RAG pipeline at ~1,000× per-query cost. **2026 consensus: per-feature decision, not an architecture religion.**

**Benchmarks:**
- **LaRA (ICML 2025):** 2,326 cases, 11 LLMs — *neither RAG nor long-context is a silver bullet*; optimal choice depends on model capability, context length, task type, retrieval quality.
- **Li et al. 2025 (arXiv 2501.01880):** LC generally beats RAG on dense structured corpora (Wikipedia/books, single-fact); RAG wins on fragmented/dialogue/general queries; **summarization-based retrieval ≈ LC**, chunk-based retrieval lags.
- **Token-tax study (2026):** long-context prompting 73.1% vs semantic RAG 65.4% accuracy — but at **26× the per-query token cost**.
- **1M-token multi-hop study (May 2026):** single-needle retrieval *solved* at 1M (Gemini 3.1 Pro, Claude Opus 4.7, GPT-5.5 = 100%). Multi-hop reveals three decay signatures: **stable** (Claude/Gemini, ≥80% through 512K), **late-cliff** (GPT-5.5, Qwen3.6 collapse 512K→1M), **smooth-decline** (DeepSeek V4 Pro). GraphRAG front-ends rescue cliff/smooth models.

**Decision axes (2026 best practice):**
1. **Freshness** — RAG indexes absorb minute-old changes; LC requires re-prompting + cache invalidation.
2. **Attribution/provenance** — RAG returns citable chunks; LC synthesis has no provenance trail.
3. **Tail-risk** — LC fails *silently* (confident wrong answers); RAG fails *loudly* (no chunks / wrong chunks → can refuse or escalate). RAG is not more correct — it's "more honestly wrong."
4. **Cost** — LC hinges on prefix caching (reads at 0.1×, but 2026's 5-min TTL raised effective costs 30–60%); RAG carries index maintenance (~20% of monthly inference spend) + retrieval pipeline ops. Practitioner range: long-context stuffing ≈ **8–82× more expensive** than retrieval per query on typical workloads.

**Practical rules of thumb (2026):**
- Under ~200K tokens of stable corpus with <~500 queries/day → **long-context + caching wins** (vector-DB hosting alone often exceeds LC API spend).
- Over ~500K tokens / >5K queries/day → **RAG wins** (a few thousand retrieved tokens vs the full corpus each query).
- Expect **~⅓ long-context, ⅓ RAG, ⅓ hybrid** across a product's features; re-ask every 6 months.
- Beware OpenAI-style long-context surcharges (GPT-5.4 docs: prompts >272K input tokens can push the session into a more expensive input tier permanently).
- **Memory systems (MemGPT/Letta)** are the third leg: state that must persist across sessions/long horizons lives outside the window (memory blocks, files, vector recall) and is pulled in on demand — the window is *working memory*, not *storage*.

---

### 1.6 Context Engineering for Agents Specifically

**Framing (Anthropic, Sept 2025):** context engineering = "the set of strategies for curating and maintaining the optimal set of tokens during LLM inference." Core model: a finite **attention budget** — every added token depletes it ("smallest possible set of high-signal tokens that maximize the likelihood of some desired outcome").

**Just-in-time context (vs pre-inference retrieval):** agents keep lightweight identifiers (file paths, queries, links) and pull content into context at runtime via tools (Claude Code: `grep`, `tail`, targeted DB queries). Enables **progressive disclosure** — discover → activate → execute. Trade-off: slower than pre-computed retrieval; needs good tool design.

**Per-agent context budgets:** Claude Code 200K budget = ~8K system + 5–10K tools + 16K output reserve + ~170K history; every section competes for the same pool (a knapsack problem). Anthropic's Context Editing API formalizes the three layers:
- **Compaction** (whole-transcript summary; trigger default 150K)
- **Tool-result clearing** (sub-transcript surgery; trigger 100K, keep 3 tool uses) — drop re-fetchable payloads, keep the record
- **Memory tool** (write notes outside the window; survives compaction/session resets)

Measured: context editing alone **+29%**, with memory **+39%**, token consumption **−84%** (100-turn web search).

**Context handoff between agents (multi-agent systems):**
- **The 15× rule** (Anthropic's research system, June 2025): agents use ~4× chat tokens; multi-agent ~15×. Token usage alone explains **80% of performance variance** on BrowseComp. Multi-agent beats single-agent Claude Opus 4 by 90.2% on research evals — *the parallelism is incidental; the context isolation is the point*.
- **Handoff = compression boundary:** subagents explore in tens of thousands of tokens, return only **1,000–2,000-token distilled summaries**. Don't pipe raw traces back (that's the "telephone game").
- **Artifact systems:** subagents write structured outputs to files/storage; coordinator gets lightweight references — avoids copying large outputs through conversation history.
- **Decompose by context, not problem type** (Jan 2026 guidance): splitting planner/implementer/tester roles loses context at each handoff; split only where context can be truly isolated (parallel research threads, clean-interface components, blackbox verification). Multi-agent overhead: 3–10× tokens; parallel writers on shared state corrupt each other — prefer read-only intelligence feeding one writer.
- **Context isolation triggers:** subtask generates >1,000 tokens, mostly irrelevant to the parent.
- **ITR (arXiv 2026):** retrieve instructions *and tools* per step — **95% per-step context reduction, +32% correct tool routing, 70% episode cost cut**; agents run 2–20× more loops within limits. System prompts + tools can consume 90% of the window in monolithic agents.

**State vs context (Letta/MemGPT):** clean architectural separation — **persistent substrate** (all state between calls: memory blocks, history, files, config) vs **ephemeral context projection** (what the model sees each call, assembled on demand). Memory blocks are size-limited, agent-editable, shareable units pinned in the window; recall/archival memory lives outside. The context window is a *materialized view*, not storage. Evict ~70% of messages on compaction to preserve continuity.

---

### 1.7 Structured Outputs (JSON mode, Function Calling, Constrained Decoding) as Context Engineering

Structured outputs are context engineering because the **output schema is context**: it defines what the model thinks about, in what order, with what vocabulary — and tool schemas occupy a big share of the window.

**Reliability ladder:**
| Level | Method | Schema validity |
|---|---|---|
| 1 | Prompt engineering ("respond only with JSON") | 80–95% |
| 2 | Function calling / tool-use schemas | 95–99% (schema is a *hint*, not a constraint) |
| 3 | **Constrained decoding** (logit masking vs FSM of the schema) | 100% by construction |

**Provider landscape (2026):**
- **OpenAI strict mode** (`response_format: json_schema, strict: true`): server-side constrained decoding; near-zero latency overhead; narrowest schema (≤5 nesting levels, ≤100 props, no `oneOf` at root, no `additionalProperties: true`, all fields required).
- **Anthropic:** no native strict mode — force tool use with `tool_choice`; ~99% conformance, rest caught by app-side validation + retry. Broader JSON Schema dialect, weaker guarantee.
- **Outlines (dottxt):** logit-level constraint for *local/open-weight* models (transformers, llama.cpp, vLLM, mlx-lm); supports JSON, regex, enums, full CFGs/EBNF. **98% schema adherence vs 76% post-generation validation; up to 5× faster generation** via constrained decoding; 0 retries. Overhead: 10–40% generation latency on complex schemas; 5,000-value enums compile to multi-MB regexes (split into two-step extraction).
- **Instructor:** Pydantic validation + automatic retry with the validation error fed back into the prompt; works with all cloud APIs; 3M+ monthly downloads. Retry cost is real: 5% initial failure × 100K requests/day = 5K extra calls/day. Cap retries at 3.
- **XGrammar (MLC):** token-mask generation <40 µs/token; one-time FSM build 50–200 ms; <5% latency impact on simple schemas, 30–60% on deep nesting.
- **Streaming:** `partial-json` (npm) / `jiter` (Python) parse incomplete JSON for progressive UI.

**Guidance:** schema (Zod/Pydantic) as single source of truth, compile to JSON Schema at the API boundary, add a validation layer even under strict mode (constrained decoding guarantees *syntax*, not *semantics* — "confidence: 1.7" passes the schema). Large enums and deep nesting are the reliability killers; enums drift across providers (Claude treats enums as suggestions).

---

### 1.8 System Prompt Engineering: Context Packing, Dynamic Prompts, Prompt Weighting

**Anthropic's system-prompt guidance:** write at the "right altitude" (steering heuristics, not brittle hardcoded logic); structure into XML/Markdown sections (`<background_information>`, `<tool_guidance>`, `<output_format>`); minimal, non-overlapping tools (bloated tool sets create ambiguous decision points and eat the window).

**Claude Code's prompt assembly pipeline (the canonical production implementation):**
- ~250 prompt fragments assembled from 8 sources into 17 sections: **8 static (cacheable prefix) + 9 dynamic (recomputed per turn)**.
- The static/dynamic split *is* the cache topology: static sections render first (byte-identical → 90% cache discount); volatile sections (MCP state) last so changes don't invalidate the prefix. This one ordering choice cuts system-prompt processing cost ~90%.
- Sub-agents get ~3 KB prompts vs the main agent's ~20 KB (7× smaller, ~85% cheaper per turn — sub-agents spawn often).
- Budget checked every turn with a 13K safety margin; token counts are estimates (tokenizer error → HTTP 413 without margin).
- **System reminders:** ~50 fragments injected mid-conversation to re-anchor behavior near the current task (combats "instruction weight loss" as context grows).

**Dynamic system prompts (research + practice):**
- **ITR** (above): per-step retrieval of instruction fragments + tools; 95% token reduction.
- **ACE (MSR, Feb 2026):** contexts as *evolving playbooks* (generate → reflect → curate); **+10.6% on agent benchmarks, +8.6% finance**; fixes "context collapse" (iterative rewriting eroding detail) and brevity bias; works offline (system prompts) and online (agent memory).
- **Assembly patterns:** template+slots → conditional inclusion → ordered injection → size-aware assembly (budget check; drop history first, low-ranked retrieval next, never core sections). Budget ≈ 60–70% of window for input.

**Prompt weighting / attention manipulation:**
- **Positional weighting is free leverage:** put highest-signal material at the start or end (lost-in-the-middle); retrieved chunks sorted by relevance with the strongest nearest the task.
- **Attention sinks (StreamingLLM):** initial tokens anchor attention; 4 sink tokens suffice — relevant to why system prompts (at the front) hold disproportionate influence, and why eviction must preserve them.
- **AGENTS.md/CLAUDE.md as context packing:** Jan 2026 study (10 repos, 124 PRs) — presence of AGENTS.md → **28.64% median runtime reduction, 16.58% output-token reduction** with comparable completion. MCP + AGENTS.md donated to the Linux Foundation's Agentic AI Foundation (Dec 2025).

**Industry signals:** Gartner (July 2025): "context engineering is in, prompt engineering is out." Redis survey (2026): 73% of leaders say agents fail more from broken context than broken models; 83% say fresh context matters more than more parameters; 94% say compounding context intelligence is essential, but only 4% have built it; 79% call context very important. Cognizant committed to training 1,000 context engineers (Aug 2025). The survey of the field: arXiv 2507.13334 (first comprehensive context-engineering taxonomy, 1,400+ papers).

---

## 2. Decision Framework — When to Use Each

| Problem / workload shape | Primary technique | Why | Watch out for |
|---|---|---|---|
| Small stable corpus (<200K tokens), few queries/day, single-pass analysis | **Long-context prompting** | Simplest; vector-DB hosting cost alone exceeds LC spend | Cache TTL expiring; OpenAI >272K-token surcharge; mid-window accuracy |
| Large/fast-changing corpus, citations required, per-user access control | **RAG + contextual retrieval** | Scalable, fresh, auditable, fails loudly | Retrieval misses; chunk-boundary loss (fix with contextual retrieval) |
| Multi-hop / cross-document synthesis | **Hybrid**: retrieve → rerank → pack full relevant docs → reason in long context | Combines grounding with synthesis depth | Over-engineering; brittle packing rules |
| Knowledge must survive sessions/hours of agent work | **Memory systems** (memory blocks, notes, recall store) | Window is working memory, not storage | Defining what persists, for whom, how long |
| Long-running single conversation approaching limits | **Compaction + tool-result clearing** | Whole-transcript vs surgical; +29–39% measured | Overly aggressive compaction loses subtle context; tune summary prompt |
| Repetitive prefixes (agent loops, RAG on fixed docs, chat) | **Prompt caching** (explicit breakpoints, pinned TTLs) | 90% read discount; >3 reuses/TTL → net win; not billed to rate limits (Anthropic) | Write premiums with <3 reuses; byte-exact prefixes; 2026 5-min default TTL |
| High-volume structured extraction / tool calls | **Constrained decoding / strict mode** (OpenAI strict, Outlines local) + validation layer | 100% syntactic validity; no retry storms | Schema complexity caps; semantic errors still possible |
| Context grows from tool bloat, not conversation | **Sub-agent context isolation** (read-only explorers returning 1–2K summaries) | Keeps parent window clean; avoids 15× multi-agent bill | Telephone-game losses; parallel writes corrupt state |
| Massive parallel research / info exceeds one window | **Multi-agent orchestrator-worker** | +90.2% on research evals; 15× tokens is the price | Only for high-value tasks; decompose by context not role |
| Model can't handle long context (cliff/smooth decay) | **Retrieval/GraphRAG front-end** | Moves 1M-equivalent performance into stable regime | Model-specific decay signatures — measure, don't assume |
| KV-cache memory bound at inference (self-hosted) | **Eviction policies** (StreamingLLM sinks, KeyDiff, SnapKV) | 22× speedup; <0.04% drop at 23% cache cut | Sink eviction collapses windowed attention |
| System prompt + tools eating 90% of window | **Dynamic prompt assembly (ITR-style)** | 95% per-step reduction; +32% tool routing | Recall of rarely-needed instructions; safety overlay always on |

**Order-of-adoption rule of thumb (progressive, from the 2026 sources):**
1. Start with long-context + a **declared token budget** (60–70% of window) + prompt caching.
2. Add compaction/clearing/memory once histories grow.
3. Add RAG when freshness + scale + provenance become requirements (start with contextual embeddings — best cost/benefit; add BM25, then reranking).
4. Add sub-agents for context isolation before full multi-agent; go multi-agent only when one window genuinely can't hold the job and the 15× bill is justified.
5. Adopt strict-mode structured outputs on any pipeline that parses LLM output.

---

## 3. Production Architecture Implications (Cost, Latency, Quality)

**Cost:**
- **Caching is the dominant input-cost lever.** Cache reads at 0.1× vs 1.0–1.25×; a cached agent loop can run ~11× cheaper per turn ($0.000219 first call vs $0.000019 subsequent on Sonnet). But caching is a *bet on traffic structure*: break-even ≈ 2–3 reads per TTL window; low-reuse prefixes are pure loss; the 2026 Anthropic 5-min default silently raised bursty workloads 30–60%. **Track `cache_read / (reads + writes + fresh)` as a tier-1 metric.**
- **Long-context economics:** input cost scales linearly with corpus × queries. Cached long-context Q&A saved 78% vs uncached (50K-token doc, 1K queries/day: $155/day → $34/day). RAG carries index-maintenance costs (~20% of inference spend) and infra, which dominate at low volume.
- **Multi-agent is 3–15× token spend** — budget it as a deliberate, value-justified expense; token spend is the biggest performance lever (80% of variance), so it buys quality when spent on isolation rather than coordination.
- **Structured outputs remove retry tax** (0 retries under constrained decoding vs 1–3 calls per failure under validation-retry); per-call ~$0.002 reranking and 100–200 ms are the price of contextual-retrieval headroom.
- Gemini explicit caching adds a storage meter ($1/M tokens/hour) — only for ≥32K-token contexts reused over hours.

**Latency:**
- Prefill scales with context length: every turn pays for the full pile even when most is stale — clearing/compaction cut prefill directly (84% token reduction in the 100-turn eval).
- Caching cuts TTFT (up to 80% on >10K-token prompts); cache misses on cold start hurt high-concurrency bursts (parallel writes all paying premium).
- Constrained decoding: near-zero overhead (OpenAI strict, XGrammar <40 µs/token) vs 10–40% on complex local grammars — flatten schemas.
- Million-token calls: 30–60× slower than tuned RAG pipelines; reranking adds 100–200 ms; sliding-window/StreamingLLM gives 22× decode speedup when applicable.

**Quality:**
- **Context rot is the default failure mode** — models degrade with length, distractors, and low query overlap even with perfect retrieval. Curated small context beats comprehensive large context on most tasks ("a well-managed 16K outperforms a poorly-managed 128K").
- **Lost-in-the-middle is real and exploitable:** place key info at edges; rerank retrieved chunks so the strongest land nearest the task; anchor system instructions at the front.
- Contextual retrieval is the highest-leverage RAG upgrade (−35–67% retrieval failures) at one-time ingestion cost.
- Compaction/clearing/memory measured +29–39% on agentic search; sub-agent isolation +90.2% on research evals; AGENTS.md −28.6% runtime / −16.6% output tokens.
- **Architecture principle (2026):** persistent substrate vs ephemeral projection — assemble a cache-topology-aware prompt (stable→volatile), enforce explicit per-region token budgets, and make context assembly a first-class, instrumented component (log assembled contexts, cache hit rate, compaction events, retry rates). Model choice is a commodity; the context layer is the differentiator (79–97% of leaders agree context > model).

---

## 4. Sources

**Context management / context rot / benchmarks**
- Chroma, *Context Rot: How Increasing Input Tokens Impacts LLM Performance* (Jul 2025) — https://research.trychroma.com/context-rot · https://github.com/chroma-core/context-rot
- Adobe, *NoLiMa: Long-Context Evaluation Beyond Literal Matching* (ICML 2025) — https://arxiv.org/abs/2502.05167 · https://github.com/adobe-research/NoLiMa
- NVIDIA, *RULER* — https://github.com/nvidia/RULER · https://arxiv.org/abs/2404.06654
- Liu et al., *Lost in the Middle* (2023) — https://arxiv.org/abs/2307.03172
- *Context Length Alone Hurts LLM Performance Despite Perfect Retrieval* (EMNLP 2025 Findings) — https://aclanthology.org/2025.findings-emnlp.1264.pdf
- *Efficient Streaming Language Models with Attention Sinks* (ICLR 2024) — https://arxiv.org/abs/2309.17453 · https://hanlab.mit.edu/projects/streamingllm
- *KeyDiff: KV Cache Eviction* (2025) — https://arxiv.org/abs/2504.15364
- vLLM experimental session KV eviction — https://github.com/vllm-project/vllm/pull/43374

**Contextual retrieval**
- Anthropic, *Contextual Retrieval* (Sep 2024) — https://www.anthropic.com/engineering/contextual-retrieval
- Claude Cookbook, *Contextual embeddings guide* — https://platform.claude.com/cookbook/capabilities-contextual-embeddings-guide
- InfoQ summary (Sep 2024) — https://www.infoq.com/news/2024/09/anthropic-contextual-retrieval/

**Prompt caching**
- Anthropic docs — https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- OpenAI docs — https://developers.openai.com/api/docs/guides/prompt-caching · OpenAI pricing — https://developers.openai.com/api/docs/pricing
- OpenAI, *Prompt Caching in the API* (Oct 2024) — https://openai.com/index/api-prompt-caching/
- OpenAI cookbook, *Prompt Caching 201* — https://developers.openai.com/cookbook/examples/prompt_caching_201
- Gemini docs: implicit caching — https://ai.google.dev/gemini-api/docs/interactions/caching · explicit caching — https://ai.google.dev/gemini-api/docs/generate-content/caching · pricing — https://ai.google.dev/gemini-api/docs/pricing
- Tokenpricing.dev, *Prompt caching deep dive: Anthropic, OpenAI, Gemini* — https://tokenpricing.dev/prompt-caching/
- Tian Pan, *Prompt Cache Break-Even math* (Apr 2026) — https://tianpan.co/blog/2026-04-17-prompt-cache-break-even-math
- Tanay Shah, *Anthropic prompt cache in production* (Apr 2026) — https://tanayshah.dev/blog/anthropic-prompt-cache-production-patterns/
- Brandon Wie, *Claude Code TTL regression* (May 2026) — https://brandonwie.dev/posts/anthropic-prompt-cache-ttl
- Romain Lespinasse, *5-minute vs 1-hour cache tier* (May 2026) — https://www.romainlespinasse.dev/posts/choosing-prompt-cache-tier/
- Ssimplifi, *Anthropic prompt caching explained* (May 2026) — https://ssimplifi.com/blog/anthropic-prompt-caching-explained

**Context compression**
- Microsoft LLMLingua / LongLLMLingua / LLMLingua-2 — https://github.com/microsoft/LLMLingua · https://arxiv.org/abs/2403.12968 · https://aclanthology.org/2024.acl-long.91/ · https://aclanthology.org/2024.findings-acl.57/
- ICAE (ICLR 2024) — https://arxiv.org/abs/2307.06945 · https://github.com/getao/icae
- AutoCompressor (EMNLP 2023) — https://aclanthology.org/2023.emnlp-main.232/

**Long context vs RAG / memory**
- LaRA benchmark (ICML 2025) — https://proceedings.mlr.press/v267/li25dv.html
- Li et al., *Long Context vs. RAG: An Evaluation and Revisits* — https://arxiv.org/abs/2501.01880
- Hamilton et al., *The Token Tax of Epistemic Accuracy* (2026) — https://arxiv.org/html/2606.20898
- *Retrieval and Multi-Hop Reasoning in 1M-Token Context Windows* (May 2026) — https://arxiv.org/html/2605.02173v1
- Tian Pan, *Long-Context vs RAG in 2026* — https://tianpan.co/blog/2026-04-27-long-context-vs-rag-2026-decision-tree
- SitePoint, *Long Context vs RAG: 1M token windows* (Feb 2026) — https://www.sitepoint.com/long-context-vs-rag-1m-token-windows/
- Open-TechStack, *RAG vs Long Context 2026* — https://open-techstack.com/blog/rag-vs-long-context-2026/
- FlowVerify, *Context rot for production LLM engineering* (May 2026) — https://www.flowverify.co/blog/context-rot-production-llm-engineering

**Context engineering for agents**
- Anthropic, *Effective context engineering for AI agents* (Sep 2025) — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Anthropic, *Managing context on the Claude Developer Platform* (Sep 2025) — https://claude.com/blog/context-management
- Anthropic cookbook, *Compaction, tool-result clearing, memory* — https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools
- Anthropic, *How we built our multi-agent research system* (Jun 2025) — https://www.anthropic.com/engineering/multi-agent-research-system
- Anthropic, *When to use multi-agent systems (and when not to)* (Jan 2026) — https://claude.com/blog/building-multi-agent-systems-when-and-how-to-use-them
- Claude Agent SDK — https://claude.com/blog/building-agents-with-the-claude-agent-sdk
- ITR, *Dynamic System Instructions and Tool Exposure* — https://arxiv.org/abs/2602.17046
- Letta, *Anatomy of a Context Window* (Jul 2025) — https://www.letta.com/blog/guide-to-context-engineering/ · *Context Constitution* — https://www.letta.com/constitution/ · *Memory Blocks* — https://www.letta.com/blog/memory-blocks/ · *Agent Memory* — https://www.letta.com/blog/agent-memory/ · *Stateful Agents* — https://www.letta.com/blog/stateful-agents/
- SwirlAI, *State of Context Engineering in 2026* (Mar 2026) — https://www.newsletter.swirlai.com/p/state-of-context-engineering-in-2026
- Redis, *The State of Context Engineering 2026* (Jul 2026) — https://redis.io/resources/state-of-context-engineering-2026/
- Wire, *Sub-agent context isolation fixes context rot* (Jun 2026) — https://usewire.io/blog/sub-agent-context-isolation-fixes-context-rot/
- John Young, *The Isolation Gate* (Jun 2026) — https://jyoung.dev/blog/multi-agent-context-isolation/
- Brendan Sechter, *State of Context Engineering, Feb 2026* — https://sgeos.github.io/ai/ai-tools/development/developer-productivity/2026/02/09/context_engineering.html

**Structured outputs / constrained decoding**
- 567-labs/Instructor — https://github.com/567-labs/instructor/
- dottxt-ai/Outlines — https://github.com/dottxt-ai/outlines
- AWS, *Structured output with Outlines* (Feb 2026) — https://aws.amazon.com/blogs/machine-learning/generate-structured-output-from-llms-with-dottxt-outlines-in-aws/
- Tian Pan, *Structured Outputs in Production* (2025) — https://tianpan.co/blog/2025-10-11-structured-outputs-in-production
- Contra Collective, *Instructor vs Outlines 2026* (Apr 2026) — https://contracollective.com/blog/instructor-vs-outlines-structured-llm-output-2026
- Cadence, *Structured outputs in production: lessons learned* (May 2026) — https://cadence.withremote.ai/blog/structured-outputs-llm-production

**System prompt engineering / context packing**
- Anthropic, *Effective context engineering* (system-prompt guidance, above) — https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- Inside Claude Code, *Prompt Assembly Pipeline* — https://y-agent.github.io/inside-claude-code/03-prompt-assembly.html
- Sourcegraph, *Context Engineering: A Practical Guide* (May 2026) — https://sourcegraph.com/blog/context-engineering
- SurePrompts, *Dynamic Context Assembly Patterns* (Apr 2026) — https://sureprompts.com/blog/dynamic-context-assembly-patterns
- Zylos Research, *Dynamic Context Assembly and Projection Patterns* (Mar 2026) — https://zylos.ai/research/2026-03-17-dynamic-context-assembly-projection-llm-agent-runtimes/
- MSR, *ACE: Agentic Context Engineering* (Feb 2026) — https://www.microsoft.com/en-us/research/publication/agentic-context-engineering-evolving-contexts-for-self-improving-language-models/
- *A Survey of Context Engineering for LLMs* (arXiv 2507.13334) — https://arxiv.org/abs/2507.13334
- Karpathy, *Context Engineering Patterns* — https://contextpatterns.com/
