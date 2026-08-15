# Advanced Context Engineering & Attention Economics for Production LLM Agents
### Research report — compiled August 2026, for the Iris context-engineering portfolio project

---

## Executive summary

Context engineering has displaced prompt engineering as the load-bearing discipline in production agent work (Anthropic, Sep 2025; Gartner, 2026). The core shift: the context window is no longer treated as a "bucket to fill" but as a **finite, rival attention budget that degrades as it is spent** — before the rated window limit is ever reached. The single most important empirical finding of 2025–2026 is **context rot**: Chroma's study (Jul 2025) showed all 18 tested frontier models (GPT-4.1, Claude 4, Gemini 2.5, Qwen3) get *less* reliable as input length grows, with non-gradual accuracy cliffs. Reported numbers: 200K-token windows losing serious accuracy by 50K tokens; accuracy drops of 30+ points when the answer sits in document positions 5–15 of 20 rather than first/last. Practical guidance across every source converges on: **find the smallest set of high-signal tokens that maximize the likelihood of the desired outcome** (Anthropic's framing).

---

## 1. Attention economics / context window economics

### Key concepts
- **Context as currency**: Tokens have moved "beyond simple data units" to being treated as the foundational currency of agent systems (arXiv Token Economics survey, 2026: "Token Economics for LLM Agents: A Dual-View Study", arXiv:2605.09104).
- **Rival, excludable resource**: The context window is contested by system prompts, tool schemas, conversation history, retrieved documents, and reasoning scratchpads. "Admitting one additional retrieved token necessarily evicts one token of history — a constrained resource allocation problem" (Token Economics survey).
- **Quadratic cost**: Doubling prompt length ≈ 4× attention compute (O(n²)). 100K tokens = 10,000× the compute of 1K tokens (multiple sources; jeemstudio, redis.io).
- **Attention vs. capacity**: The relevant constraint is the model's *attention budget*, not the raw window size. Bigger windows raise the ceiling but don't remove context pollution (Anthropic).
- **Carrying cost vs. stockout cost**: Memory design trades off "carrying cost" (stored context displacing reasoning capacity) vs. "stockout cost" (missing critical history degrades decisions) — an inventory-management framing (Token Economics survey).
- **Working-set thinking**: Engineers who think in cache terms (eviction, prefetching, write-back, hot path) pick up context engineering fastest — "most of the patterns are just cache management" (iotdigitaltwinplm.com).

### Reported numbers
- 1 token ≈ 0.75 English words; 1 token ≈ 4 chars (startups.com).
- System prompt typical budget: 500–2,000 tokens; conversation history is the main window-filler (8–15K tokens after 10 turns); response budget needs 2,000–4,000 tokens minimum; target 80% utilization ceiling, not 100% (bodegaone.ai).
- A 32K window can fill in 10–15 minutes of a coding session; 128K gives comfortable headroom (bodegaone.ai).
- ~65% of enterprise AI failures in 2025 attributed to context drift or memory loss during multi-step reasoning (Zylos research, citing industry analyses).
- KV cache for long contexts consumes hundreds of GB per request; serving throughput drops 10–100× vs short contexts; "geometric cost escalation" (Zylos 2026).
- Agent token spend variance: some runs consume 10× more tokens than others on equivalent tasks, driven almost entirely by search efficiency, not coding ability (Morph, citing Zylos/METR-adjacent studies).
- METR (Mar 2025): LLM ability on long-horizon tasks is improving steadily; the "35-minute wall" — every agent's success rate decreases after ~35 minutes of human-equivalent task time, and doubling task duration quadruples failure rate (Zylos/Morph).

### Production implications
- Budget context per session, per call, and per component (system/tools/history/retrieval/output); enforce an 80% ceiling with headroom for tool outputs.
- Measure cost per *completed task*, not per call; a leaner context needing one extra retrieval round-trip can still win on total task cost (iotdigitaltwinplm.com).
- Treat prompt caching as a design constraint that changes what "long system prompt" means (see §2).
- Trace everything: log assembled context per call (or hash + segment manifest); a per-session context report (token totals, layer breakdown, compaction events, cache hit rate, retrieval round-trips) should run weekly on sampled sessions.

### Sources
- https://arxiv.org/abs/2605.09104 (Token Economics for LLM Agents, 2026 survey)
- https://zylos.ai/en/research/2026-05-27-context-window-economics-persistent-agents
- https://zylos.ai/research/2026-01-19-llm-context-management
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents (Sep 29, 2025)
- https://iotdigitaltwinplm.com/context-engineering-llm-agents-production-2026
- https://www.bodegaone.ai/blog/context-window-planner-for-local-llms
- https://www.startups.com/lexicon/context-window
- https://redis.io/blog/llm-context-windows/
- https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/

---

## 2. Prompt caching strategies for multi-agent systems

### Key concepts
- **Core rule: stable prefix, dynamic suffix.** Most caches reward exact prefix reuse. Recommended ordering (agents-best-practices repo): 1) tool definitions in deterministic order, 2) static system/developer instructions, 3) stable scoped instructions/skill index, 4) stable reference context, 5) append-only conversation/event history, 6) dynamic runtime env, 7) current user message. Dynamic values (timestamps, request IDs, working dir, cursor state, fresh search results, newest user message) belong at the end.
- **Deterministic serialization**: cache stability depends on byte-level request shape — stable tool order, stable JSON key order, stable schema formatting, versioned prompt/tool bundles.
- **Cache-killing anti-patterns**: timestamps at prompt start, request ID in the stable prefix, randomized tool order / JSON key order, per-user secrets in the prefix, rewriting conversation history every turn, re-summarizing the whole session every turn, changing schema formatting without versioning.
- **Provider mechanics**:
  - Anthropic: explicit `cache_control` breakpoints; min 1,024 tokens per breakpoint; up to 4 checkpoints/request; TTL 5 min (extended to 1 h with regular hits); cache writes cost 1.25× (5-min) or 2× (1-h) base price; cache reads 0.1× base (90% discount); ~5 conversation turns cacheable; workspace-level cache isolation since Feb 2026.
  - OpenAI: automatic prefix caching ≥1,024 tokens, 50% input discount, ~5–10 min TTL (up to an hour off-peak); concurrent first-time requests don't hit cache (must prime).
  - Google: implicit caching (auto since May 2025), no code changes.
- **Multi-agent/cache-aware design**: KVFlow (NeurIPS 2025) showed LRU-based KV eviction is suboptimal under agentic workflows; workflow-aware eviction + overlapped KV prefetching gives up to 1.83× (single workflow, large prompts) and 2.19× (many concurrent workflows) speedups vs SGLang hierarchical radix cache. Long fixed few-shot prompts (3,000+ tokens) are prime caching targets; dynamic parts aren't.
- **Cache architecture hierarchy**: semantic cache (100% savings, bypasses LLM) → prefix cache (50–90%) → full inference. ~31% of LLM queries are semantically similar to previous ones (Introl).

### Reported numbers
- Anthropic: up to 90% input cost reduction, up to 85% latency reduction on cached prompts; example: 100K-token book summary 11.5s → 2.4s. Cache reads $0.30/M vs $3.00/M fresh (Sonnet 4.6).
- Break-even: ~2 cache hits per cached prefix (Anthropic pricing; 1.4 reads per Introl).
- Combined caching + batch API (50% off): 95% reduction from base rate; legal-context example $45K/mo → <$2.3K/mo for 50K documents.
- Real-world team example: $8,000/mo with caching vs $45,000 without (82% reduction, same workload).
- 78.5% effective input-cost reduction at 1 write : 10 reads; approaching 90% floor at 100 reads.
- "Long system prompts are now economically rational": a 50K-token system prompt with 90% hit rate costs less than a 5K-token prompt with no caching.
- vLLM Automatic Prefix Caching: PagedAttention gives 14–24× throughput vs naive; hash-based KV block reuse.
- GitHub issue estimate for multi-agent caching: Agent 2 turn 1 with cache: 50K cached + 12K new (~$0.08) vs 62K fresh (~$0.31); Agent 1 turn 2: ~$0.02.

### Production implications
- Design the context builder, tool registry, instruction manager, compactor, and telemetry *as a caching system*: cache hit rate is a first-class health metric.
- Track: hit rate by session/tenant, prompt & tool bundle hash counts, cost split (uncached/cached input/output), latency split (prefill/TTFT/generation), hit rate before/after compaction. Alert on zero cached tokens over many turns.
- For multi-agent orchestrators: share stable prefixes across agents (LangGraph/CrewAI parallel agents get cache hits automatically when sharing global context); use tree-structured radix caching to deduplicate shared prefix segments.

### Sources
- https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- https://www.anthropic.com/news/prompt-caching
- https://github.com/DenisSergeevitch/agents-best-practices/blob/main/references/prompt-caching-and-cost.md
- https://arxiv.org/abs/2507.07400 (KVFlow, NeurIPS 2025)
- https://agentmarketcap.ai/blog/2026/04/06/prompt-caching-economics-2026-anthropic-google-agent-cost
- https://introl.com/blog/prompt-caching-infrastructure-llm-cost-latency-reduction-guide-2025
- https://niteagent.com/blog/prompt-caching-production-guide/
- https://docs.vllm.ai/en/stable/design/prefix_caching/
- https://arxiv.org/html/2601.06007v2 (Evaluation of Prompt Caching for Long-Horizon Agentic Tasks, Jan 2026)

---

## 3. Context distillation & agent knowledge compression

### Key concepts
Two distinct things share the name — keep them separate:
1. **Model distillation** (train a smaller model on a bigger one's outputs) — well-known; Anthropic's Feb 2026 announcement describes *illicit* distillation attacks (DeepSeek/Moonshot/MiniMax generating 16M+ exchanges to extract Claude capabilities). Not the subject here.
2. **Context distillation (prompt-level)** — Anthropic's 2025 technique for long-horizon agents: "compaction... distills the contents of a context window in a high-fidelity manner, enabling the agent to continue with minimal performance degradation." The model summarizes message history, preserving architectural decisions, unresolved bugs, and implementation details while discarding redundant tool outputs.

### Technique catalog (2025–2026)
- **Compaction**: summarize a conversation nearing the window limit, reinitialize a fresh window with the summary. First lever for long-term coherence (Anthropic, Sep 2025). Implemented natively in Claude Code and other platforms ("native compaction and tool-result-clearing features").
- **ACON (arXiv:2510.00615, 2025)**: learns a *failure-driven compression guideline* from agent failures and distills it into a smaller compressor — compression policy informed by what actually went wrong.
- **"Less Context, Better Agents" (arXiv:2606.10209, 2026)**: for tool-heavy single-session workflows, a lightweight recency window + compact running summary suffices — no external store or retriever needed; evaluated end-to-end on a live enterprise ERP with a hard success criterion (zero residual).
- **Context as a Tool (arXiv:2512.22087, 2025)**: context management for long-horizon SWE agents.
- **ACE — Agentic Context Engineering (arXiv:2510.04618, Stanford/SambaNova/UC Berkeley)**: Generator → Reflector → Curator roles maintain an evolving "playbook" context via incremental delta updates (bullets with metadata), preventing both *brevity bias* (contexts get rewritten too short) and *context collapse* (iterative rewriting compresses accumulated context into uninformative summaries).
- **CoMem (ICML 2026)**: decouples memory management from the agent loop; a smaller "memory model" summarizes in parallel (k-step-off asynchronous pipeline), masking summarization latency.
- **Multi-level/batch summarization**: hierarchical summarization claims 91% information retention at 68% size reduction (Zylos, 2026).
- **Meilisearch framing (Jun 2026)**: context distillation as a *pipeline*: retrieve → narrow by relevance/confidence → summarize/chunk/restructure → feed as generation prompt → iterate. Claims 50–70% token reduction in practice (thefastapplycompany.com).
- **Agent knowledge compression workflows**: rolling summaries, structured note-taking (see §6), extract-then-retrieve (facts, entities, decisions persisted outside the window), sub-agent result distillation (only ~1,000–2,000-token summaries return to the lead agent).

### Reported numbers
- "Context distillation... reduces tokens by 50–70% while preserving quality" (Morph/thefastapplycompany, Feb 2026).
- Multi-level summarization: 91% retention at 68% size reduction (Zylos 2026).
- ACE: +10.6% on agent benchmarks, +8.6% on finance; 86.9% lower adaptation latency; up to 83.6% lower rollout cost.
- CoMem: 1.4× latency improvement on SWE-Bench-Verified while preserving performance.
- Sub-agent summaries: ~1,000–2,000 tokens per Anthropic guidance (dayfing.dev).
- Claude Code restricts tool responses to 25,000 tokens by default (Anthropic tool-writing guide).

### Production implications
- Compaction is a *policy* decision: what to keep (decisions, unresolved bugs, plans) vs. what to evict (raw tool outputs, dead ends). Don't let the summarizer re-summarize the whole session every turn (kills cache, invites collapse).
- Choose per-task: compaction (back-and-forth flows), note-taking (milestone-driven work), sub-agents (parallel exploration).
- Consider async/dedicated compressors (CoMem pattern) when latency of in-loop summarization matters.
- Distillation in the 2026 security sense matters: don't naively pipe privileged model outputs into training pipelines (see Anthropic's distillation-attack disclosure).

### Sources
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://arxiv.org/abs/2510.00615 (ACON)
- https://arxiv.org/abs/2606.10209 (Less Context, Better Agents — efficient context engineering for long-horizon tool-using agents)
- https://arxiv.org/abs/2512.22087 (Context as a Tool)
- https://arxiv.org/abs/2510.04618 (ACE: Agentic Context Engineering)
- https://icml.cc/virtual/2026/poster/60825 (CoMem)
- https://www.anthropic.com/news/detecting-and-preventing-distillation-attacks (Feb 23, 2026)
- https://www.meilisearch.com/blog/context-distillation
- https://www.thefastapplycompany.com/context-distillation
- https://zylos.ai/research/2026-01-19-llm-context-management

---

## 4. Long-context evals: does long context actually work?

### Key concepts
- **Lost in the Middle (Liu et al., TACL 2024, arXiv:2307.03172)**: U-shaped performance curve; models attend strongly to start and end of context, poorly to the middle. Established with 20-document contexts.
- **Context rot (Chroma, Jul 2025)**: term formalized — "performance varies significantly as input length changes, even on simple tasks." 18 models (GPT-4.1, Claude 4 Opus/Sonnet, Gemini 2.5, Qwen3), 5 experiment types (needle-question similarity, distractor interference, haystack structure, LongMemEval conversational QA, repeated-word), 194,480 total LLM calls, 8 input lengths × 11 needle positions, LLM-as-judge with >99% human agreement.
- **NIAH criticism**: Needle-in-a-Haystack (Kamradt 2023) is "a simple lexical retrieval task" that overstates real long-context reliability; it tests *presence of information*, not *usable synthesis* (Chroma; bytebytego).
- **Shape of degradation**: not gradual — "models can maintain near-perfect accuracy up to a certain context length, then performance drops off a cliff unpredictably" (bytebytego, Apr 2026). Some models 95% → 60% across a length threshold.
- **Position-dependence changes with fill level** (Veseli et al., 2025, arXiv:2508.07479): U-shape only holds when context <50% full; >50% full, degradation is by *distance from the end* (recency dominates).
- **Length itself hurts, even with perfect retrieval** (EMNLP 2025 findings, "Context Length Alone Hurts LLM Performance Despite Perfect Retrieval"): degradation persists even when all distractors are masked and retrieval is perfect; shortcutting to short context improves GPT-4o on RULER by up to 4%.
- **Effective context research**: "maximum effective context window" for real-world tasks is far below advertised (Paulsen 2025, arXiv:2509.21361): "real-world use cases should focus on limiting token count." Most long-context models show sharp drops past ~32K tokens on realistic tasks (redis.io). Zylos: most models "break 30–40% earlier than claimed" (e.g., 130K actual vs 200K claimed).
- **Agentic evals 2026**: LOCA-bench (ICML 2026) extends context to infinity in controlled fashion while keeping task semantics fixed — evaluates *models + scaffolds* (context management strategies improve success rates substantially). Classifier Context Rot (arXiv:2605.12366, May 2026): safety monitors degrade on long transcripts — frontier monitors (Claude Opus 4.6, GPT-5.4, Gemini 3) go blind on very long agent transcripts; prompting mitigations (thinking tokens, reminders, incremental monitor calls) help; fine-tuning GPT-4.1 on long-context classification didn't generalize.
- **Enterpret/Zep adjacent**: Zep's temporal knowledge-graph memory (Graphiti, arXiv:2501.13956) beats full-conversation baselines on LongMemEval (conversations averaging ~115K tokens): 94.8% vs 93.4% (MemGPT) on DMR; ~90% latency reduction while maintaining accuracy. Zep frames the finding as: retrieval beats stuffing — external memory avoids context rot rather than fixing it.

### Reported numbers
- Liu et al.: with 20 retrieved documents (~4K tokens), accuracy drops from 70–75% to 55–60%; position 1 ≈ 75% vs position 10 ≈ 55% (redis.io context-rot explainer).
- Chroma: all 18 models degrade; degradation accelerates as needle-query semantic similarity decreases; distractors hurt more at length; coherent haystack structure hurts more than random text.
- Position 5–15 of 20 documents: 30+ accuracy-point penalty vs first/last (cruxdigits, citing Chroma-consistent numbers).
- Accuracy cliffs before rated limits: "200K window showing serious accuracy loss by 50K tokens."
- 65% of enterprise AI failures in 2025 attributed to context drift/memory loss (Zylos).
- LongMemEval conversations average ~115K tokens (Zep).

### Production implications
- Benchmark *your* model at *your* operating context length; don't trust NIAH numbers or rated windows. A model fine at 32K may fall apart at 500K.
- Don't stuff: RAG that retrieves 50 docs can beat 5-doc retrieval; more docs = worse answers past a point (saturation/degradation in RAG, Cuconasu et al. 2024).
- Prefer position engineering: put critical facts near the start (recency also works late in session); restructure before compressing when "data present but ignored."
- For safety/monitoring agents: run incremental monitor calls (not one end-of-session pass); add thinking tokens and task reminders to long transcripts.
- Treat memory systems (Zep-style knowledge graphs, vector stores) as the mitigation layer for rot, not an optional extra.

### Sources
- https://arxiv.org/abs/2307.03172 (Lost in the Middle, TACL 2024)
- https://research.trychroma.com/context-rot (Chroma technical report, Jul 2025)
- https://arxiv.org/abs/2509.21361 (Paulsen: Maximum Effective Context Window)
- https://arxiv.org/abs/2508.07479 (Veseli et al. 2025: context degradation pattern by fill level)
- https://aclanthology.org/2025.findings-emnlp.1264.pdf (Context Length Alone Hurts)
- https://arxiv.org/abs/2605.12366 (Classifier Context Rot)
- https://arxiv.org/html/2602.07962v1 / https://icml.cc/virtual/2026/poster/64486 (LOCA-bench)
- https://arxiv.org/abs/2501.13956 (Zep temporal KG architecture)
- https://redis.io/blog/context-rot/
- https://blog.bytebytego.com/p/a-guide-to-context-engineering-for

---

## 5. Context engineering for tool calling

### Key concepts
- **Tool definition bloat ("MCP Tax")**: every tool schema injected into context costs tokens per turn. GitHub's engineering team measured: a 40-tool GitHub MCP server adds 10–15 KB of schema per turn; three enterprise servers (GitHub + Slack + data warehouse) = 30,000+ tokens of metadata before a single reasoning step (scalekit.com, citing github.blog).
- **Anthropic's own measurements**: five-server setup — GitHub 35 tools (~26K tokens), Slack 11 (~21K), Sentry 5 (~3K), Grafana 5 (~3K), Splunk 2 (~2K) = 58 tools ≈ 55K tokens; Jira adds ~17K; "we've seen tool definitions consume 134K tokens before optimization."
- **Fix 1 — On-demand tool discovery (Tool Search Tool, Anthropic, Nov 2025)**: `defer_loading: true`; only the ~500-token search tool is loaded upfront; 3–5 relevant tools (~3K tokens) expanded on demand. Result: ~8.7K total context vs 72–77K traditional — an 85% reduction in tool-related token usage, preserving ~95% of the window. Accuracy gains: Opus 4 49%→74%, Opus 4.5 79.5%→88.1% on MCP evals with large tool libraries.
- **Fix 2 — Programmatic Tool Calling**: agent writes code to process large data (e.g., Excel with thousands of rows) instead of pulling data into context.
- **Fix 3 — Tool Use Examples**: few-shot correct invocations to reduce wrong-parameter failures.
- **Fix 4 — Credential hygiene**: credentials in schemas are both waste (80–120 tokens/param) and an OWASP LLM02/LLM07 exposure (scalekit); empirical study found 89.6% of embedded-credential cases exploitable (arXiv:2604.03070). Resolve credentials at execution time via vault-backed injection.
- **Fix 5 — Token-efficient tool responses**: pagination, range selection, filtering, truncation with sensible defaults; Claude Code caps tool responses at 25K tokens; steer agents toward many small targeted searches instead of one broad one; engineer error strings to be actionable.
- **Tool selection quality**: most common failures are wrong tool selection and wrong parameters, especially with similar names (`notification-send-user` vs `notification-send-channel`); namespacing tools; prompt-engineering descriptions; run evals measuring accuracy, runtime, call counts, token consumption, tool errors (Anthropic "Writing effective tools for agents").
- **Parallel tool calls**: models emit multiple `tool_use` blocks per turn; parallel execution cuts wall-clock; results must be kept token-lean or they bloat the next turn.
- **Tool-result clearing**: after using a tool result, drop it from history (native in Claude Code / Claude platform since 2026); a single tool call returning a 50K-token JSON payload is a prime bloat source.
- **Large result offloading / Code Mode**: aggregate/filter/transform in code (Python in sandbox calling MCP tools), print only summaries — avoids both context bloat and hallucinated arithmetic (TrueFoundry).
- **GitHub Copilot's result**: cut agentic workflow token costs by 62% on production CI workflows via tool pruning, description compression, lazy loading (github.blog).

### Production implications
- Right-size tool catalogs: prune unused tools, compress descriptions, defer-load rarely used ones; namespace to avoid collisions.
- Design tools like APIs for agents: `search_contacts`/`message_contact` over `list_contacts` (skip brute-force reads).
- Track per-tool-call token spend; tool traffic is invisible to LLM-only monitoring (traefik.io).
- Keep schemas credential-free; inject auth at execution.
- Compaction + tool-result eviction are the first two levers that prevent the most common slow degradation.

### Sources
- https://www.anthropic.com/engineering/advanced-tool-use (Nov 24, 2025)
- https://www.anthropic.com/engineering/writing-tools-for-agents (Sep 11, 2025)
- https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools (Mar 2026)
- https://www.scalekit.com/blog/token-efficient-tool-calling
- https://github.blog/ai-and-ml/github-copilot/improving-token-efficiency-in-github-agentic-workflows/
- https://www.truefoundry.com/docs/agent-platform/agent-harness/context-engineering/overview
- https://redis.io/blog/prompt-bloat-llm-apps/
- https://arxiv.org/abs/2604.03070 (empirical study of agent skills credential exposure)

---

## 6. Multi-agent context sharing

### Key concepts
- **Three canonical memory topologies** (MAS-memory survey, cited in Token Economics): agent-local memory (efficient, silo-prone), shared pools (fast knowledge transfer, but "tragedy of the commons" — pollution with low-relevance info), hybrid with access control; open challenge: coordinated forgetting.
- **Blackboard architecture** (revived for LLM MAS, arXiv:2507.01701): central shared structure (the blackboard) + knowledge-source agents + control unit. Each selected agent takes the whole blackboard as its prompt and writes back; no direct peer messaging; removes the per-agent memory module. Control is content-driven (who acts next depends on board state). Trade-off: controller loops, slower coordination than direct messaging.
- **LbMAS** (arXiv:2510.01285): agents communicate solely through the blackboard; reduces overall prompt length across agents, enabling more discussion under token constraints.
- **Shared memory patterns** (LangGraph StateGraph, CrewAI, Redis): blackboard (shared workspace + specialists + control); hub-and-spoke with orchestrator mediating; View Projector / view projection — critical: you cannot dump full shared state into every agent's window; project role- and budget-aware views.
- **State serialization specifics** (123ofai.com system-design guide):
  - Reducers per field type: append for lists, last-writer-wins for scalars (almost always a bug on lists), merge for dicts.
  - Lost-update problem: use optimistic concurrency with version checks; CRDT-based merge or LLM-arbiter semantic merge for complex fields.
  - Version your state schema — checkpoints from older schema versions crash or corrupt silently.
  - Shared memory is for *state*, not *events*: use a message/event bus alongside for point-to-point messaging.
  - Bottlenecks: 50KB state × 10 serializations × 10 agents = 5MB JSON processing per step; 50KB/checkpoint × 10 steps × 10K sessions/day ≈ 5GB/day — budget TTL cleanup. Redis handles ~100K ops/sec ≈ 50–100 concurrent sessions.
- **MAST study (UC Berkeley)**: 79% of multi-agent system failures stem from specification and coordination problems, not model capability or infrastructure.
- **Sub-agent isolation as context management**: Claude Code docs call subagents "a context-management tool" first, parallelism tool second — each subagent runs in a fresh window; only ~1,000–2,000-token summaries return to the lead. Parent's brief to the sub-agent is itself a context-engineering artifact.
- **Hot/warm/cold hierarchy**: hot = working memory in window, warm = near-term store, cold = archived but retrievable (instinctools).
- **Mem0 multi-agent memory guidance** (Mar 2026): governs how agents store, retrieve, share, coordinate context; versioned, reviewable written state — "persisting bad context makes the next step worse" (Atlan).

### Production implications
- Design the shared state schema up front (typed fields, reducers, versioning); don't let shared memory grow unboundedly — project views per agent.
- Prefer sub-agent isolation + distilled returns over full-context fan-out; only distilled results (1–2K tokens) return to the lead.
- Use a controller/orchestrator for shared pool access to avoid pollution; implement coordinated forgetting/eviction.
- Separate state (shared memory) from events (message bus).
- For high-stakes/regulated work, checkpoint state for time-travel debugging and human-in-the-loop resumption (LangGraph checkpointing; Redis langgraph-checkpoint).

### Sources
- https://arxiv.org/html/2507.01701 (Blackboard LLM multi-agent systems)
- https://arxiv.org/abs/2510.01285 (LbMAS)
- https://mem0.ai/blog/multi-agent-memory-systems (Mar 2026)
- https://123ofai.com/qnalab/system-design/blocks/shared-memory
- https://mat.umai-tech.com/architectures/v6 (blackboard architecture card)
- https://www.instinctools.com/blog/context-engineering/
- https://atlan.com/know/ai-agent/context-engineering/context-engineering-techniques-ai-agents/
- https://www.techaheadcorp.com/blog/context-rot-problem/ (MAST 79% stat)

---

## 7. Streaming + incremental context

### Key concepts
- **Streaming semantics**: token delivery via SSE / HTTP chunked / WebSocket / gRPC; perceived latency = time-to-first-token (TTFT), not end-to-end. 2026: streaming is default for chat and voice agents; unit of measurement shifts from "request" to "stream segment".
- **Agentic streaming payoffs** (changegamer.ai): (1) TTFT — act on early output; (2) early cancellation — abort on hallucinated first tokens or wrong tool before paying full generation; (3) incremental parsing — tool-call args arrive as partial JSON; two strategies: accumulate-then-parse (safe, simple) vs streaming partial-JSON parsers (`partial-json`, Pydantic partial parsing) for early field access.
- **Streaming + caching tension**: streaming needs incremental emission; caching needs complete responses. Production pattern: on cache miss stream tokens live and asynchronously store the completed response; on hit return cached instantly without streaming.
- **Incremental context / KV extension**: "Revisable by Design" (arXiv:2604.23283) — theory of streaming agent execution where injections arrive mid-stream and the agent decides whether to revise. "Efficient LLM Serving for Agentic Workflows" (arXiv:2603.16104, Mar 2026): multi-agent debates redundantly reprocess shared history; serving should *extend the KV cache* — "converting costly recomputation into lightweight incremental updates." Calls out passive prefix caching as insufficient for agentic workloads.
- **Guardrails on streams** (futureagi): content-safety on full output is too late — half a violating message may already be streamed; post-guardrails need buffering; mid-stream guardrail design differs.
- **Observability**: log stream start, first-token, complete-token timestamps; alert on TTFT directly (a streaming endpoint with slow first-token is still slow).
- **CoMem (ICML 2026)** extends the idea: overlap memory-model summarization with agent inference (asynchronous pipeline) to mask context-processing latency.

### Reported numbers
- Streamed response: tokens visible in ~200 ms vs 1.5s decode non-streamed; p99 4.2s measured vs 380 ms perceived (futureagi).
- CoMem: 1.4× latency improvement, gains scale with throughput.
- FlashAttention-3: 1.3 PFLOPs/s on H100; constant-latency long-context attention variants claim 2.7× speedup at 128K and 35× at 2M vs full attention (Zylos).

### Production implications
- Stream every agent step to tracing so humans can intervene mid-trajectory.
- Use partial-JSON parsing for long structured outputs; cancel streams early on garbage.
- Architect for KV-cache extension across agent turns (avoid re-prefixing shared history each turn) — this is where multi-agent serving differs from plain chat.
- Combine streaming + semantic caching for perceived-speed wins.

### Sources
- https://changegamer.ai/resources/streaming-for-agents
- https://futureagi.com/glossary/streaming
- https://redis.io/blog/streaming-llm-responses/
- https://arxiv.org/pdf/2603.16104 (Efficient LLM Serving for Agentic Workflows)
- https://arxiv.org/pdf/2604.23283 (Revisable by Design: streaming agent execution theory)
- https://icml.cc/virtual/2026/poster/60825 (CoMem)

---

## 8. Token-efficient prompting techniques: structure formats

### Key concepts
- **Format matters — up to ~40%**: model performance can vary dramatically (up to 40%) based purely on prompt format; different models prefer different formats (GPT-3.5 → JSON, GPT-4 → Markdown) (Daniel Voyce analysis, cited in RDD10+).
- **XML**: Anthropic explicitly recommends XML tags — "Claude was specifically tuned to pay special attention to your structure." XML wins for complex, multi-section, hierarchical prompts (tested beyond Claude: Llama-3.1 405B also preferred XML for complex prompts). Unambiguous open/close delimiters; survives tokenization issues that break whitespace/indentation-based structure; namespaced tags avoid collisions with content; fences untrusted input (prompt-injection defense: model less likely to follow instructions inside `<document>`).
- **Markdown**: more token-economical (~15% fewer tokens than equivalent JSON; leaner than XML due to single-symbol syntax); reads naturally; strong instruction-following signal because models were trained on massive volumes of READMEs/docs. Weakness: ambiguous boundaries; `## Document 1` collisions when documents contain their own headings; fragile to whitespace/tokenization.
- **Emerging consensus (2025–2026)**: hybrid — Markdown for instruction sections, XML tags for fencing data/untrusted input, minified JSON for structured data payloads. "The larger and more complex the prompt, the more XML tends to outperform."
- **Word salad vs structure**: raw/undifferentiated prompts underperform; LLMs "read a flat sequence of tokens and infer structure from training patterns" — structure only works if the model has seen that pattern consistently (teachyou.ai).
- **Structured scratchpads**: give agents an explicit notes/state object region that survives compaction — externalizes intermediate reasoning without bloating history (Anthropic; iotdigitaltwinplm.com).
- **Marker economy** (from cache-best-practices): deterministic ordering, versioned bundles, stable whitespace — structure is also a *cache* decision (see §2).

### Reported numbers
- Markdown ≈ 15% more token-efficient than JSON (community benchmark); Markdown vs HTML: headings ~8→2 tokens (75% saving), tables ~40→15 (63%), lists ~25→8 (68%) (markdowntoolsonline).
- Claude ~12% more likely to adhere to all specified elements/constraints with XML vs Markdown (third-party testing reported in algorithmunmasked).
- Format choice alone: up to 40% performance variation across models (Daniel Voyce / RDD10+).

### Production implications
- Match format to model: XML for Claude, and for complex hierarchical instructions generally; Markdown for simple/instruction-heavy prompts; JSON for data the model should treat as records (never ask for Markdown-fenced JSON if code parses it).
- Minify data JSON in prompts; keep instruction prose readable.
- Always fence untrusted user content; always close XML tags (unclosed tag is worse than none).
- Test formats empirically per model + task; treat the prompt as a compiled, versioned artifact.

### Sources
- https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering (XML guidance)
- https://www.robertodiasduarte.com.br/en/markdown-vs-xml-em-prompts-para-llms-uma-analise-comparativa
- https://www.teachyou.ai/blog/xml-vs-markdown-prompts
- https://jsonkit.in/blog/json-vs-markdown-llm-prompts
- https://community.openai.com/t/markdown-is-15-more-token-efficient-than-json/841742
- https://arxiv.org/abs/2411.10541 (Does Prompt Formatting Have Any Impact on LLM Performance?)
- https://markdowntoolsonline.com/blog/markdown-for-ai-and-llm-prompts

---

## 9. Model Context Protocol (MCP) as a context/access layer

### Key concepts
- **Origin & governance**: open-sourced Nov 25, 2024 by Anthropic; donated Dec 2025 to the Linux Foundation's Agentic AI Foundation (AAIF), co-founded with Block and OpenAI; backed by AWS, Google, Microsoft, Salesforce, Snowflake. Now "de facto tool-calling standard" for LangChain, LlamaIndex, AutoGen, CrewAI.
- **Current spec (2025-11-25)**: server features = resources, prompts, tools; client features = sampling, roots, elicitation; two standard transports — stdio and Streamable HTTP (older HTTP+SSE deprecated). Local integrations typically stdio (client launches server as subprocess).
- **Adoption numbers (verified)**: 10K+ active public servers (Anthropic, Dec 2025); 9,652 registry records / 28,959 version records (registry API, May 2026); 15,926 GitHub `mcp-server` topic repos; 97M+ monthly SDK downloads; modelcontextprotocol/servers repo 86K+ stars. Stacklok 2026 survey: 41% of surveyed software orgs in limited/broad production with MCP servers (replaces unsourced "78%" claim). Market projection $10.3B by 2025, 34.6% CAGR (CData/Forrester).
- **Three classes of servers** (Medium/MCP-Hive, Feb 2026): internal organizational servers (largest volume, invisible in directories), vendor-built integrations (GitHub, Stripe, Atlassian, Salesforce), community general-purpose servers (publicly visible, variable quality).
- **2026 extensions**: MCP Apps (Feb 2026); "next generation" stateless MCP (Cloudflare, Aug 2026: servers can run in a Worker, no stateful infra).
- **MCP as a context/access layer**: MCP is a *discovery protocol* — servers advertise tool catalogs that land in context, which is exactly why it is the epicenter of prompt bloat (MCP Tax, §5). Registry + discovery solves "how do I find the server"; it does not solve "how do I keep its schemas out of my window" (needs tool-search/deferral, §5).
- **Security landscape**: fragmentation, low trust and discoverability (Elastic, Jun 2025); taxonomy of runtime faults (arXiv:2606.05339); SoK on security/safety (arXiv:2512.08290); measurement study (arXiv:2509.25292); authentication gaps and static keys (Astrix 2025).

### Production implications
- Use MCP for tool/resource access with registry-based discovery; expect catalog bloat and plan tool filtering/deferral at the harness layer.
- Prefer Streamable HTTP for remote servers; stdio for local dev.
- Treat MCP servers as untrusted third-party code: validate schemas, scan for static keys, scope with `roots`.
- Watch A2A (agent-to-agent) for inter-agent communication; MCP remains the model-to-tool layer.

### Sources
- https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation (Dec 9, 2025)
- https://modelcontextprotocol.io/specification/2025-11-25
- https://www.digitalapplied.com/blog/mcp-adoption-statistics-2026-model-context-protocol
- https://medium.com/mcp-server/the-rise-of-mcp-protocol-adoption-in-2026-and-emerging-monetization-models-cb03438e985c
- https://stacklok.com/wp-content/uploads/2026/01/State-of-MCP-in-Software-2026_FINAL.pdf
- https://blog.cloudflare.com/mcp-v2/ (Aug 2026)
- https://arxiv.org/pdf/2606.05339v1 (taxonomy of MCP runtime faults)
- https://www.cdata.com/blog/2026-year-enterprise-ready-mcp-adoption

---

## 10. 2026 state of the field & breakthrough signals

### Key concepts
- **"Context engineering is in, prompt engineering is out"** — Gartner directive to AI leaders (Redis State of Context Engineering 2026, Jul 2026). The field consolidated in ~8 months after two anchor posts: Manus's rebuild lessons (Jul 2025) and Anthropic's guide (Sep 2025) (Kushal Banda, Towards AI).
- **ICML 2026 (Seoul)**: "agentic AI" in 60 of 247 accepted workshop proposals — an unprecedentedly agentic program; LOCA-bench and CoMem both at ICML 2026.
- **Observability products for agent context**: AWS CloudWatch Coding Agent Insights (Jul 2026) — telemetry from Claude Code, Codex, GitHub Copilot — evidence context rot became "a line item engineering leaders now track."
- **Agentic context engineering (ACE, arXiv:2510.04618)**: the agent curates its own context (Generator/Reflector/Curator + delta playbooks) instead of engineers hand-tuning — +10.6% agent benchmarks, 86.9% lower adaptation latency, up to 83.6% lower rollout cost. Open problems: dependency on feedback quality; KV-cache-reuse claims unvalidated; no concurrency handling.
- **Compression breakthroughs**: neural/recurrent context compression aiming at 68% size reduction with 91% retention; test-time-training (TTT-E2E) style "constant latency regardless of context length" — "the research community might finally arrive at a basic solution to long context in 2026" (Zylos, cautious).
- **Classifier context rot (arXiv:2605.12366, May 2026)**: safety monitors degrade with transcript length; incremental monitor calls + thinking tokens mitigate; fine-tuning does not reliably generalize.
- **The 35-minute wall** and 10× token variance (see §1) — long-horizon reliability became the framing metric for 2026 agent work.
- **Chrome of the trend**: "cheap 1M-token context just landed" — but "Do you still need to manage your agent's context?" is answered *yes* by every 2026 source: bigger windows postpone the cliff, they don't remove it (dreaming.press; cruxdigits).

### Production implications
- Expect model-side context management to improve (search-native tools, tool search, native compaction), but keep harness-level budgets, eviction, caching, and tracing — they're now table stakes.
- Budget for agent-specific observability (per-step tokens, cache hit rate, compaction events, tool-call tokens).
- Stay model-agnostic at the context layer where possible (framework-level context management > model-dependent behavior).
- The 2026 skill ceiling is *systemic*: context assembly pipelines with relevance gating, pruning, budget checks, and compaction triggers (iotdigitaltwinplm.com pattern catalog).

### Sources
- https://redis.io/resources/state-of-context-engineering-2026/ (Jul 2026)
- https://arxiv.org/abs/2510.04618 (ACE)
- https://cruxdigits.nl/blog/context-engineering-ai-agents-2026/
- https://pub.towardsai.net/state-of-context-engineering-in-2026-cf92d010eab1
- https://icml.cc/virtual/2026/poster/64486 (LOCA-bench)
- https://arxiv.org/abs/2605.12366 (Classifier Context Rot)
- https://www.techtimes.com/articles/319684/20260704/icml-2026-opens-monday-seoul-agentic-ai-tops-record-year-peer-review-strains.htm
- https://www.morphllm.com/context-rot

---

## Appendix A: Canonical reference list (for portfolio citations)

1. Anthropic — *Effective Context Engineering for AI Agents* (Sep 29, 2025): the field-defining document. https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
2. Chroma — *Context Rot* technical report (Jul 14, 2025). https://research.trychroma.com/context-rot
3. Liu et al. — *Lost in the Middle* (TACL 2024). https://arxiv.org/abs/2307.03172
4. Anthropic — *Advanced tool use* (Tool Search Tool, Programmatic Tool Calling, Tool Use Examples; Nov 24, 2025). https://www.anthropic.com/engineering/advanced-tool-use
5. Anthropic — *Writing effective tools for AI agents* (Sep 11, 2025). https://www.anthropic.com/engineering/writing-tools-for-agents
6. KVFlow (NeurIPS 2025) — workflow-aware prefix caching. https://arxiv.org/abs/2507.07400
7. ACON — failure-driven context compression. https://arxiv.org/abs/2510.00615
8. ACE — Agentic Context Engineering. https://arxiv.org/abs/2510.04618
9. LOCA-bench (ICML 2026). https://arxiv.org/html/2602.07962v1
10. CoMem (ICML 2026). https://icml.cc/virtual/2026/poster/60825
11. Zep/Graphiti — temporal KG agent memory. https://arxiv.org/abs/2501.13956
12. Token Economics for LLM Agents survey. https://arxiv.org/abs/2605.09104
13. MCP spec 2025-11-25 + AAIF announcement. https://modelcontextprotocol.io/ ; https://www.anthropic.com/news/donating-the-model-context-protocol-and-establishing-of-the-agentic-ai-foundation
14. Mei et al. — *A Survey of Context Engineering for Large Language Models*. https://arxiv.org/abs/2507.13334
15. Prompt caching docs: Anthropic https://platform.claude.com/docs/en/build-with-claude/prompt-caching ; OpenAI https://platform.openai.com/docs/guides/prompt-caching ; Google https://ai.google.dev/gemini-api/docs/caching

## Appendix B: Open problems worth flagging in the portfolio piece

- Effective context length remains far below advertised for every model family; no production model has eliminated positional bias (bytebytego).
- Context rot is "an architectural property of transformer attention, not a capability gap training solves" (Morph).
- Cache-aware prompt design and context engineering are mutually reinforcing but rarely designed together (agents-best-practices; KVFlow).
- Distillation/compaction quality is the weak link: "persisting bad context makes the next step worse" (Atlan); ACE's Reflector quality is an open research problem.
- Multi-agent shared memory lacks standard concurrency semantics (optimistic locking, CRDTs, view projection) — a genuine engineering gap (123ofai).
