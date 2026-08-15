# Iris — workspace

This directory is Iris's **memory workspace** — the source of truth. Everything
Iris remembers lives here as plain, human-readable Markdown. There is no hidden
state: if it's not on disk, Iris doesn't know it.

```
AGENTS.md              ← human-written instructions (identity contract, always injected)
USER.md                ← who YOU are: profile, preferences, communication style (curated)
MEMORY.md              ← durable facts & decisions about your life (curated by dreaming)
DREAMS.md              ← her dream diary: what consolidation changed and why (human-readable)
memory/YYYY-MM-DD.md   ← daily notes: everything that happened, dated, append-only (episodic)
skills/*.md            ← procedures Iris wrote for herself (procedural memory)
config/iris.json       ← identity born during onboarding (name, personality, tone, timezone)
.dreams/               ← staging area for consolidation candidates (machine-facing)
```

**Rules:**
- MEMORY.md stays small and compact. Detail lives in `memory/`. Consolidation
  enforces this budget.
- Daily notes are append-only and never auto-injected into the prompt; they are
  searched on demand.
- Only MEMORY.md and USER.md are injected at session start (within a token
  budget) — they are the curated core.
- DREAMS.md is for reading, not for the prompt.

The Postgres index (pgvector + FTS) is a *derived* view of these files. If you
delete the database, Iris rebuilds the index from the files. Never the other
way around.