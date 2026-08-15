# Iris — Design Document

**Date:** 2026-08-15
**Status:** Approved (Approach A: LangGraph core + our own memory engine)
**Goal:** A production-grade personal AI assistant with a *visible mind* — a memory system we engineered ourselves, measured with evals, accessed via Telegram over MCP.

---

## 1. Vision

Iris is a personal daily assistant (reminders, research, notes, tracking, briefings) whose memory is the product:

- She **remembers** your life across weeks via a layered, consolidated memory engine (OpenClaw-inspired, engineered deeper)
- She **sleeps** (on demand or nightly best-effort) and consolidates; her dreams are human-readable in `DREAMS.md`
- She **forgets** — with measurable curves, supersession, and manual control
- She **learns skills** — writing her own reusable procedures into procedural memory
- She **initiates** — circadian proactivity: morning briefings, evening reflections, learned rhythm
- You can **see her mind** — web dashboard: heatmaps, forgetting curves, dream feed, eval reports
- Her **identity is born in onboarding** — name, personality, tone defined on first boot (OpenClaw-style), stored as memory artifacts
- Her **memory is proven** — a full ablation study: same tasks, memory off vs files-only vs full engine, measuring quality, cost, latency

## 2. Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Use case | General daily assistant |
| Memory engine | Build our own (not Letta/Mem0) |
| Signature tools | Memory introspection, skill-learning, circadian proactivity |
| Runtime | Local Docker, 24/7 |
| Models | Two-tier: strong (chat) + cheap (extraction/consolidation) |
| Sleep | Manual (`/sleep`, `/wake`) + best-effort nightly sweep |
| Persona | First-run onboarding wizard |
| Memory scope | All conversations stored with dates; OpenClaw-inspired (MEMORY.md, USER.md, daily notes, DREAMS.md) |
| Storage | Markdown files = source of truth + Postgres/pgvector index |
| Evals | Full ablation study (memory lab) |
| Dashboard | Web dashboard |
| Graph | LangGraph (durability, HITL, streaming) |
| Telegram | Custom MCP server; Iris is the MCP client |

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Docker Compose                        │
│  ┌────────────┐   MCP        ┌──────────────────────────┐   │
│  │  Telegram   │◄───────────►│        IRIS CORE         │   │
│  │  MCP Server │  stdio/HTTP │  (Python, uvicorn)       │   │
│  └────────────┘              │  ┌────────────────────┐  │   │
│  ┌────────────┐              │  │ LangGraph runtime  │  │   │
│  │  Postgres   │◄───────────►│  │ chat/command/sleep │  │   │
│  │ + pgvector  │             │  │ scheduler (APS)    │  │   │
│  └────────────┘              │  └────────────────────┘  │   │
│  ┌────────────┐              │  ┌────────────────────┐  │   │
│  │  Dashboard  │◄───────────►│  │ OUR MEMORY ENGINE  │  │   │
│  │ (FastAPI)   │             │  │ write/recall/dream │  │   │
│  └────────────┘              │  └────────────────────┘  │   │
│  Workspace files (truth):                                │   │
│   MEMORY.md · USER.md · memory/YYYY-MM-DD.md ·           │   │
│   DREAMS.md · skills/*.md · config/iris.json             │   │
└─────────────────────────────────────────────────────────────┘
```

**Services:** `iris-core` (uvicorn + LangGraph + memory engine), `telegram-mcp` (MCP server wrapping the bot), `postgres` (pgvector), `dashboard` (FastAPI + charts). All in one Docker Compose.

## 4. Memory Engine (ours)

### 4.1 Tiers (files = source of truth)

| Tier | File(s) | Written by | Injected | Notes |
|---|---|---|---|---|
| Instructions | `AGENTS.md` | human only | always, session start | identity contract |
| Curated core | `MEMORY.md` (facts/decisions), `USER.md` (profile: preferences, comm style, relationships) | dreaming consolidation; explicit `remember` | always, budgeted (cache-friendly stable prefix) | small, compact |
| Episodic | `memory/YYYY-MM-DD.md` | agent during work; memory flush | never auto-inject; searchable | append-only, dated; all conversations stored with dates |
| Prospective | standing intents (SQLite table) + scheduled jobs | `intent` tool / scheduler | only when trigger fires | reminders, watchers |
| Procedural | `skills/*.md` (+ JSON metadata) | skill-learning tool | when trigger matches | self-written reusable procedures |
| Review | `DREAMS.md` | dreaming phases | never; human reading | dream diary |

### 4.2 Index (Postgres + pgvector)

- Chunks (~400 tokens, 80 overlap) of MEMORY.md, USER.md, memory/*.md
- Per-chunk metadata: origin class (`owner`/`agent`/`untrusted`), observation date, importance (1–10, scored at write by model-in-the-loop writer), supersession key, trigger phrases, source file + line range
- Hybrid retrieval: pgvector (cosine) + Postgres FTS (BM25) fused → score = hybrid × exponential recency decay (30-day half-life; MEMORY.md/USER.md evergreen) × importance → MMR diversity (deterministic, local)
- Trigger injection: fast lexical+vector prefilter per inbound message; ≥0.72 match; max 3 entries; compact hidden context block; **only curated tier qualifies** (security property)

### 4.3 Write path (off the hot path)

- After each turn: cheap model extracts candidate memories (ADD/UPDATE/DELETE/NO-OP signals) from the turn pair
- Candidates staged in `memory/.dreams/staging/` with provenance (untrusted/system excluded from promotion structurally)
- Explicit "remember this" → direct write to MEMORY.md with `owner` provenance
- Daily note append: session transcript flush at session end (compaction memory-flush pattern)

### 4.4 Sleep / dreaming (on demand + scheduled best-effort)

Three phases:
1. **Light** — dedupe staged signals, score candidates by weighted signals (relevance, recall frequency, query diversity, recency, multi-day recurrence, conceptual richness); deterministic gate before any model call
2. **REM** — theme reflections: group candidates into themes, draft consolidated statements with evidence citations (Generative Agents pattern)
3. **Deep** — consolidation rewrite of MEMORY.md: duplicates merged, superseded entries retired via supersession keys, compact wording, source references (daily-note anchors) preserved; **optimistic concurrency** (content hash re-check before atomic rename; append fallback); pre-image stored; human-readable diff summary appended to DREAMS.md

### 4.5 Recall lanes (cost-split)

- **Default lane** (every turn): deterministic hybrid × decay × importance, MMR — no model call, no added latency
- **Escalation lane** (only when deterministic conditions fail: temporal/multi-hop questions): sub-agent turn that searches daily notes + transcripts, reads files, synthesizes an answer

### 4.6 Forgetting

- Ebbinghaus-style decay curves for episodic content (dashboard-visible)
- Supersession keys retire stale facts (invalidated, not deleted — provenance preserved)
- `/forget` command with HITL confirmation → supersession, not hard delete
- Memory rot detection: entries unreferenced beyond threshold flagged in dreams

## 5. Agent Graph (LangGraph)

- **Chat graph:** Telegram event → router → ReAct loop (agent with tools) → reply; checkpointer (PostgresSaver) per thread; streaming to Telegram
- **Tools:** `memory_search`, `memory_get`, `remember` (explicit, owner provenance), `inspect_mind` (show MEMORY.md/USER.md/DREAMS.md), `forget` (HITL gate), `skill_write`/`skill_list`/`skill_apply` (procedural), `intent` (standing intents), scheduler tools (reminders), `send_message` (via MCP), `dream_now` (sleep graph trigger)
- **Sleep graph:** Light → REM → Deep → DREAMS.md append → dashboard notify
- **Onboarding graph:** wizard state machine (name → personality → tone → timezone → sleep pref) → writes config + USER.md
- **HITL:** `interrupt()` before forget/irreversible actions; resume via Telegram

## 6. Context Engineering

- Bootstrap budgets: USER.md + MEMORY.md injected with hard token caps; files kept compact (consolidation enforces)
- Stable-prefix ordering for prompt caching (system + identity + MEMORY.md + USER.md first; conversation after) — two-tier model economics
- Trigger injection (≤3 entries) keeps hot path lean
- Compaction with memory flush: when conversation exceeds budget → silent flush turn writes to daily note, then summarize
- Tool schemas kept tight (research: tool schema bloat is 69% of input tokens)

## 7. Telegram MCP Bridge

- `telegram-mcp` service: MCP server (stdio over HTTP) exposing `send_message`, `send_photo`, `send_sticker`, `receive` (event stream), bot identity
- Iris Core is the MCP client; all channel I/O through tools → framework-agnostic, and demonstrates the MCP pattern
- Long-polling Telegram Bot API (no public webhook needed; local Docker)

## 8. Dashboard (FastAPI + charts)

- `/` overview: memory size, tier stats, last sleep, active skills
- `/mind` heatmap: memory activation over time (recency × importance)
- `/forgetting`: decay curves per entry class
- `/dreams`: DREAMS.md feed
- `/eval`: ablation study reports (charts + tables)
- Read-only view of workspace files (`/files`)

## 9. Evals — Memory Lab (ablation study)

- **Task suite:** synthetic user history (multi-day conversations, evolving facts, preferences) fed through the same write path; task classes: single-hop recall, multi-hop reasoning, temporal reasoning, preference adherence, conflict/staleness detection, abstention
- **Variants:** (a) no memory, (b) files-only (retrieval, no consolidation), (c) full engine
- **Metrics:** accuracy per class, cost per run, latency per query, pass^k; confidence intervals
- **Protocol:** same model, same tools, only memory layer varies; paired significance testing
- **Output:** `research/eval-report.md` + dashboard page
- CI: golden suite rerun script + smoke tests

## 10. Deployment & Ops

- Docker Compose: postgres (pgvector/pgvector image), iris-core, telegram-mcp, dashboard
- `.env`: provider keys, model names (strong/cheap), Telegram bot token
- Observability: structured logs per node; optional Langfuse hook (phase 2)
- Reliability: retries with jitter on LLM calls, 4 timeout clocks, circuit breakers on provider errors, idempotent side effects (no duplicate Telegram sends)

## 11. Security

- Taint gating: untrusted/system provenance never promotes to curated core
- Memory files are injection surface: Iris instructed to treat file content as data, not instructions; provenance logged on every write
- Telegram token only in the bridge container; no secrets in memory files
- HITL before destructive actions

## 12. Build order (chapters)

1. Scaffold: repo, compose, config, workspace layout, README
2. Memory engine core: tiers, index, write path, recall lanes
3. Dreaming + forgetting + skills (procedural memory)
4. LangGraph runtime + chat graph + tools
5. Telegram MCP bridge + onboarding
6. Dashboard
7. Memory lab evals + CI
8. Local deploy + end-to-end
