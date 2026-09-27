"""Memory engine — our own layered memory system.

Tiers (files are the source of truth, Postgres is the derived index):
- curated core: MEMORY.md, USER.md          (always injected, budgeted)
- episodic:     memory/YYYY-MM-DD.md         (append-only, searchable)
- procedural:   skills/*.md                  (self-written procedures)
- review:       DREAMS.md                    (dream diary, human-readable)

Pipelines: write path (extraction), recall lanes (hybrid × decay × importance),
dreaming (sleep-time consolidation), forgetting (supersession + decay).
"""
