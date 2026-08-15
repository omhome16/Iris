# Eval lab — recall ablation study

Date: 2026-08-15 · synthetic corpus, deterministic embeddings, no model calls · the real `MemoryIndex.search` code path, one knob toggled per row.

| mode | recall@5 | mrr@5 | mean gold rank | vs full |
|---|---|---|---|---|
| full | 0.83 | 0.83 | 1.83 | — |
| vector_only | 1.00 | 1.00 | 1.00 | +0.17 |
| no_decay | 1.00 | 0.89 | 1.33 | +0.17 |
| no_importance | 0.83 | 0.83 | 1.83 | +0.00 |
| no_mmr | 0.83 | 0.83 | 1.83 | +0.00 |

**Reading:** recall@5 = fraction of queries whose gold fact made the top-5. mrr@5 = how early the gold fact appeared. mean gold rank = average position (6 means 'not in top-5').

**Verdict:** the spread between modes shows how much each component contributes. If `no_decay` ≈ `full`, recency isn't earning its keep on this corpus; if `no_mmr` ≈ `full`, the diversity step adds nothing; `vector_only` sets the baseline a deterministic pipeline must beat.

**Honest caveat:** the default lane is a *precision* device — decay and importance deliberately demote old, low-importance facts below fresh, important ones. On this corpus the gold facts for old/trivial queries are exactly what the pipeline is designed to hide; that trade is the documented reason Iris has an *escalation lane* (design §4.5) for temporal/multi-hop questions, which searches daily notes directly instead of relying on the default lane.

**Misses per mode:**
- `full`: what instrument did Omar learn as a child
- `vector_only`: none
- `no_decay`: none
- `no_importance`: what instrument did Omar learn as a child
- `no_mmr`: what instrument did Omar learn as a child

## Escalation lane — does it close the gap?

| metric | value |
|---|---|
| escalation recall@5 | 1.00 |
| escalation mrr@5 | 0.89 |

The escalation lane (decay disabled, daily notes only) is triggered by temporal signals or a weak default lane, and it **recovers every query the default lane missed**:
- `what instrument did Omar learn as a child`

The two lanes together answer everything the default lane alone hides — the precision trade is now a *choice*, not a blind spot.