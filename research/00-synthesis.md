# Iris — Research Synthesis (Aug 14, 2026)

Compiled from 12 parallel research agents on memory engineering, agent graph engineering, context engineering, and production AI engineering. Full reports: `01-context-engineering.md`, `02-context-engineering-2026.md` (saved), others summarized here.

---

## 1. Memory Engineering — State of the Art

**The four architectural bets (2026):**
| Architecture | Reps | Core idea | Best for |
|---|---|---|---|
| Extraction layer | Mem0 | LLM extracts durable facts; ADD/UPDATE/DELETE ops; fused multi-signal retrieval | Fast on-ramp, per-user preferences/facts |
| Temporal knowledge graph | Zep/Graphiti | Bi-temporal facts (validity windows), contradictions invalidate-not-delete, provenance to episodes | Facts that change over time; auditability |
| Self-editing agent runtime | Letta | Agent owns its context: core memory blocks, recall + archival, sleep-time consolidation | Long-horizon autonomous agents |
| Filesystem/git memory | Claude memory tool, OpenAI sandbox, Letta Code | Memory as files/MEMORY.md, git-backed | Coding agents, agents that read/write their own memory |

**Taxonomy:** working/short-term (session) · semantic (facts/preferences) · episodic (past experiences) · procedural (skills/rules).
**Operations:** write (extract) → retrieve (semantic + BM25 + entity + temporal, fused) → consolidate (offline/sleep-time: dedupe, summarize, promote episodic→semantic) → forget (decay, TTL, invalidation).
**Open problems (resume-worthy gaps):** selective forgetting & conflict resolution (<7% solved), consolidation without loss, temporal reasoning at scale, memory rot, write-path cost (often >80% of agent execution time), eval hygiene.
**Key caution from research:** simple retrieval beats complex systems at scale (MemBench); a plain filesystem scored 74% LoCoMo beating specialized tools. Vendor benchmark claims are noisy (Zep self-corrected 84→58; Mem0 claims disputed). => **Ablation-based evals are the credibility play.**

## 2. Agent Graph Engineering — State of the Art

- **LangGraph 1.x (Oct 2025, 1.2 May 2026)** = de-facto production standard (Uber, LinkedIn, Klarna, Lyft, JPMorgan). Checkpointing per superstep, typed state + reducers, interrupts/HITL, time-travel, subgraphs, Send fan-out, streaming. 34.5M monthly downloads.
- **ADK 2.0 (May 2026)** became a graph engine; **MS Agent Framework 1.0 (Apr 2026)** GA; **Temporal** became the durability layer under agents (OpenAI Agents SDK integration GA Mar 2026).
- **Consensus: agents are state machines, and state machines must be durable.** Checkpoint-per-superstep vs event-sourced replay are the two durability models.
- **Patterns:** supervisor, hierarchical (2 levels, ≤7 agents per supervisor), swarm/handoffs, plan-and-execute, reflection/evaluator-optimizer, fan-out/fan-in, HITL gates. 2026 guidance: *start single-agent, escalate with measurement*; multi-agent costs 2–15x tokens.
- **2026 production defaults:** LangGraph for stateful/durable/memory-heavy agents; MCP universal (97M monthly SDK downloads) as the tool decoupling layer.

## 3. Context Engineering — State of the Art

- **Context rot is real:** effective window ≈ 30–60% of advertised; lost-in-the-middle U-curve. Chroma study: every model degrades with length.
- **Prompt caching economics:** Anthropic reads 0.1x / writes 1.25x, break-even ~2 hits; cache-aware ordering (stable prefix first) is a hard requirement.
- **Contextual retrieval:** −67% retrieval failures with reranking; parent-child chunking is the de-facto ingestion pattern.
- **Tool schema bloat:** 69% of input tokens in production agentic apps are system prompts (tool schemas + instructions); tool search/caching cuts 62–85%.
- **Attention economics:** treat context as a finite budget; compaction + JIT retrieval + memory instead of stuffing history. Context distillation (Anthropic) for docs.
- **Decision framework:** <200K tokens static → stuff + cache; >1M or evolving → retrieval/memory; facts changing over time → temporal memory.

## 4. Production Engineering — Consensus Stack

- **Stack:** LangGraph (Python) + PostgresSaver/PostgresStore (+pgvector) + Langfuse (OSS) or LangSmith (free tier) + MCP servers + CI evals.
- **Observability:** OTel GenAI semconv (still "Development" — pin versions); trace everything: LLM calls, tool calls, handoffs, state transitions; cost/latency per node.
- **Evals pyramid:** L1 deterministic unit → L2 golden task sets + trajectory evals (tool correctness, plan adherence, error recovery) → L3 private ≥100-task suites + red teaming (OWASP Top 10 Agentic Apps, Dec 2025) → L4 online sampled judges + shadow/canary.
- **Reliability:** retries with jitter, 4 separate timeout clocks, circuit breakers (incl. token/turn cost triggers), fallback models, idempotency keys.
- **LLM-as-judge discipline:** different model family, decomposed rubric, position-bias mitigation, human gold set recalibrated monthly, κ ≥ 0.6.

## 5. Use Case Candidates (from research)

1. **Deep research agent** — canonical demo of every pattern; most documented (Anthropic, OpenAI, Google); research state as memory. Strong resume signal.
2. **Personal memory assistant / companion** — cross-session user state, preferences, evolving facts; memory IS the product.
3. **Customer support agent with memory** — Zendesk/Intercom pattern; τ²-bench as anchor; policy compliance.
4. **Coding agent with project memory** — CLAUDE.md pattern, git-backed memory (Letta Code, OpenAI sandbox).
5. **Knowledge/meeting agent** — documents → temporal KG; Bayer/Cognee pattern.
6. **Sales/deal memory agent** — deal stages, account intelligence, evolving facts (Graphiti's flagship case).

**Criteria for a great portfolio project (from research):** long-horizon tasks, personalization, evolving facts, multi-step workflows, human-in-loop, measurable evals (ablation!), durable execution showcase, cost/token economics as a first-class metric.