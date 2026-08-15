# Iris — an AI assistant with a visible mind

Iris is a production-grade personal AI assistant whose **memory is the product**.
Built to demonstrate three AI-engineering disciplines end to end:

- **Memory engineering** — a layered memory engine (Markdown soul +
  Postgres/pgvector index): curated core, episodic daily notes, procedural
  skills, dream-time consolidation, forgetting curves, taint gating.
- **Agent graph engineering** — LangGraph runtime: durable chat graph,
  sleep/consolidation graph, onboarding wizard, human-in-the-loop gates.
- **Context engineering** — bootstrap budgets, stable-prefix prompt caching,
  trigger injection, cost-split recall lanes, compaction with memory flush.

She lives in your pocket (Telegram over MCP), sleeps on command (`/sleep`),
dreams in `DREAMS.md`, teaches herself skills, and her memory is proven by a
full **ablation study** in `reports/eval_lab.md`.

---

## How it works

### The memory model (the soul)

Iris's memory is **plain Markdown files** — always human-readable, always the
source of truth. Postgres is only a rebuildable vector index over them.

```
workspace/
├── AGENTS.md              # operating contract — survives every reset
├── USER.md                # your profile (stable preferences, relationships)
├── MEMORY.md              # curated long-term memory (evergreen facts)
├── DREAMS.md              # dream diary — read-only for Iris
├── memory/YYYY-MM-DD.md   # episodic daily notes (decay over time)
├── skills/*.md            # procedural skills she writes herself
└── config/iris.json       # onboarding state / identity
```

Four provenances gate everything: **owner** (you), **agent** (her pipelines),
**telegram** (chat history), **import** (bulk files). Curated content may only
graduate from owner/agent sources.

### What happens on every chat turn

```
your message
  → route: onboarding wizard? (until you've named her) or agent
  → assemble context: USER.md + MEMORY.md (bounded token budgets)
     + hybrid recall (vector + FTS, recency decay, importance, MMR diversity)
  → agent node: strong model, tool-enabled ReAct loop
     tools: memory_search (default + escalate lanes) · remember · forget ·
            inspect_mind · skill_write/list/apply · dream_now ·
            file_create/write/read/list (sandboxed) · web_search (Tavily) ·
            ingest_url · send_message/get_chat_history (Telegram)
  → write path (off the hot path, cheap model): extract memory candidates
     → staged (tainted) → promoted only by dreaming, never directly
  → reply; checkpointer persists every step (survives restarts)
```

Every turn is bounded by a **120 s budget** — a provider hiccup can never hang
you; you get a graceful "still thinking" instead.

### Recall lanes

- **Default lane** — precision-tuned hybrid search (vector + FTS × recency
  decay × importance, MMR diversity). Best for "what do you know about X".
- **Escalation lane** — decay disabled, daily notes only, triggered by
  temporal questions ("when did…", "last month") or a weak default lane.
  Recovers the old facts the default lane deliberately hides. Proven by the
  eval lab: the two lanes together answer everything either alone misses.

### Sandboxed file tools (computer-use, jailed)

Iris may only touch `workspace/sandbox/` (configurable via `SANDBOX_DIR`).
`file_create / file_write / file_read / file_list` validate every path —
traversal (`..`), absolute paths and drive letters are rejected, so she can
organize notes and drafts without ever reaching `.env` or system files.

### Web search + document ingestion

- `web_search` — Tavily (free tier, set `TAVILY_API_KEY`); without a key it
  answers "not configured" gracefully.
- `ingest_url` — fetch any URL, strip it to readable text, store as
  `imports/YYYY-MM-DD-<hash>.md`. Imports are **UNTRUSTED origin**: recallable
  on demand, never promoted into curated memory (the trust model refuses it).

### Voice notes (Groq Whisper)

Send a Telegram voice message; the bridge downloads it and posts it to
`POST /voice`, where `groq/whisper-large-v3-turbo` transcribes it and the
graph answers on the transcript. Needs `GROQ_API_KEY` (free tier). While any
turn is in flight, Telegram shows the **typing…** indicator — she feels alive
instead of frozen during free-tier latency.

### Circadian proactivity

APScheduler runs two jobs in your timezone:
- **04:00 nightly sleep** — the dream cycle consolidates the day into `MEMORY.md`.
- **08:00 morning brief** — a Telegram digest: what dreaming promoted, how
  retention looks, what's flagged as rot. Requires `OWNER_CHAT_ID` + a
  connected channel; otherwise it stays silent.

Both are no-ops when they can't run safely — the scheduler never crashes the
process.

### Dreaming (consolidation)

`/sleep` runs a graph: **Light → REM → Deep**. Light deterministically scores
staged signals (occurrence, importance, richness, trigger-diversity) and gates
them; REM de-duplicates against existing memory (LLM); Deep writes consolidated
facts into `MEMORY.md` and retires superseded entries with keys. Memories are
promoted into the curated core **only by dreaming** — never by the write path
directly (the taint gate).

### Forgetting

Nothing is silently deleted. `retention` computes how much of each memory
survived decay (`/retention`); entries below the rot threshold are flagged
(`/rot`) and surfaced for your decision. `forget` supersedes an entry with a
marker and re-indexes — daily notes are append-only.

### The bridge: Telegram over MCP

`mcp_servers/telegram/` is a custom **MCP 2.0 server** (streamable HTTP) that
wraps the Telegram Bot API with long-polling. It exposes `send_message`,
`get_chat_history`, `broadcast` as MCP tools to Iris and forwards your inbound
messages to the agent API. It also parses slash commands
(`/start /help /mind /skills /forget /rot /retention /dream_now /sleep /wake`)
and learns your chat id from the first `/start`.

### Providers

Two-tier brain via LiteLLM (any provider works):

| Tier | Job | Gemini (default) | Groq (alternative) |
|---|---|---|---|
| strong | conversation, reasoning, skills | `gemini-3.5-flash` | `llama-3.3-70b-versatile` |
| cheap | extraction, consolidation, scoring | `gemini-3.1-flash-lite` | `llama-3.1-8b-instant` |
| embeddings | index everything | `gemini-embedding-001` | n/a — stays Gemini |
| voice | voice-note transcription | n/a | `groq/whisper-large-v3-turbo` |
| web search | current information | n/a | Tavily (free tier) |

Set `GROQ_API_KEY` in `.env` and the strong/cheap tiers swap automatically
(Groq's free tier is generous and fast). You still need `GEMINI_API_KEY` for
embeddings either way. Rate limits are handled by a client-side throttle
(strong tier), retry-with-jitter, Retry-After parsing, and graceful fallback.

---

## Running it

### Prerequisites

- Python 3.13 + [uv](https://docs.astral.sh/uv/)
- Docker (for Postgres + the full compose stack)
- A Gemini API key (free: aistudio.google.com/apikey) and optionally a
  [Groq](https://console.groq.com/keys) key
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 1. Setup

```bash
cp .env.example .env      # fill in keys (Gemini + optional Groq + bot token)
docker compose up -d postgres   # just the DB for local dev
uv sync                   # install deps into .venv
```

### 2. Run (local dev, Windows-friendly)

```bash
uv run python scripts/run_core.py      # API on :8000 (handles the Windows event-loop quirks)
uv run python mcp_servers/telegram/server.py   # Telegram MCP bridge on :8100
uv run python dashboard/app.py         # dashboard on :8080
```

Or one shot with Docker (everything, ports 8000/8080/8100 + Postgres on 5433):

```bash
docker compose up --build
```

### 3. Meet her

Message the bot on Telegram (or use the dashboard chat). The **onboarding
wizard** kicks in — you name her, pick her personality/tone/timezone/sleep
preferences, and she writes her identity to `USER.md` + `config/iris.json`.
That's the moment she's born.

### Commands

| Command | What it does |
|---|---|
| `/sleep` · `/wake` | run / pause the dream cycle |
| `/mind` | inspect what she currently knows |
| `/remember <x>` | direct memory write (owner provenance) |
| `/forget <x>` | supersede a memory (two-phase confirm) |
| `/skills` | list learned skills |
| `/rot` · `/retention` | forgetting report |
| `/dream_now` | force a consolidation pass |
| `/help` | command list |

### Tests, eval lab, CI

```bash
uv run pytest tests -q                    # 40 tests (deterministic, no API calls)
uv run python scripts/eval_lab.py         # ablation study → reports/eval_lab.md
```

CI (`.github/workflows/iris-ci.yml`) runs pytest + the eval lab against a
pgvector service container on every push.

### Reset

```bash
uv run python scripts/fresh_start.py      # wipes memory/dreams/skills/index
```

`AGENTS.md` and `.env` survive — she's a newborn again, and the next chat
re-runs onboarding.

---

## Architecture map

```
┌─────────────┐   ┌──────────────────────────────┐   ┌─────────────┐
│  Telegram   │──▶│  mcp_servers/telegram        │──▶│  iris-core  │
│  (you)      │   │  MCP 2.0 server + dispatcher │   │  FastAPI    │
└─────────────┘   └──────────────────────────────┘   │  :8000      │
        │              send_message ◀────────────────│  LangGraph  │
        │                                            └──────┬──────┘
┌───────▼────────┐   ┌──────────────┐   ┌──────────────┐    │
│ dashboard:8080 │   │  workspace/  │◀──│  memory/     │    │
│ Flask UI       │   │  Markdown    │   │  index+write │    │
└────────────────┘   │  (soul)      │   │  +dreams     │    │
                     └──────┬───────┘   │  +forgetting │    │
                            │           └──────┬───────┘    │
                     ┌──────▼───────┐          │            │
                     │ Postgres 16  │◀─────────┘            │
                     │ + pgvector   │   rebuildable index   │
                     └──────────────┘                       │
```

## Chapters (build order)

1. Scaffold
2. Memory engine core — tiers, hybrid index, write path, recall lanes
3. Dreaming + forgetting + skill learning
4. LangGraph runtime — chat graph, tools, HITL
5. Telegram MCP bridge + onboarding wizard
6. Web dashboard — the visible mind
7. Memory lab — ablation evals + CI
8. Local deploy + end-to-end verification

Research basis: `research/00-synthesis.md` (12 parallel research briefs,
Aug 2026). Design: `docs/superpowers/specs/2026-08-15-iris-design.md`.