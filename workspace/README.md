# workspace

The agent's memory workspace — the source of truth. Everything it remembers lives
here as plain, human-readable files. There is no hidden state: if it isn't on
disk, it isn't known.

```
AGENTS.md              ← your instructions to the agent (always injected, never edited by it)
USER.md                ← who you are: profile, preferences, communication style (curated)
MEMORY.md              ← durable facts and decisions (curated)
DREAMS.md              ← the consolidation diary: what changed and why (for reading)
memory/YYYY-MM-DD.md   ← daily notes: everything that happened, dated, append-only
skills/*.md            ← procedures the agent wrote for itself (procedural memory)
config/iris.json       ← profile from `iris config` (name, tone, timezone, persona)
config/llm_calls.jsonl ← the cost ledger: one line per model call
config/traces.jsonl    ← one line per turn: stages, tools, judgments
.dreams/               ← staging area for consolidation candidates
```

**Rules**

- `MEMORY.md` stays small and compact; detail lives in `memory/`.
- Daily notes are append-only and never auto-injected — they are searched on
  demand.
- Only `MEMORY.md` and `USER.md` enter the prompt at session start, inside a
  token budget.
- `DREAMS.md` is for reading, not for the prompt.

The index (SQLite FTS, or Postgres + pgvector) is a *derived* view of these
files. Delete the database and it is rebuilt from the files; never the other way
around.

**Backups:** this directory is the backup. `tar` it. `MEMORY.md`, `USER.md`,
`DREAMS.md`, `memory/` and `skills/` are personal data — keep them out of git.
