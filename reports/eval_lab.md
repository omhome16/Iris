# Eval lab — recall ablation study

Date: 2026-09-26 · synthetic corpus, deterministic embeddings, no model calls · the real `MemoryIndex.search` code path, one knob toggled per row.

`memory_off` is the **OFF baseline**: no retrieval at all. Every other row is read against it *and* against `full`, because a table that only compares variants of memory cannot show that memory is worth having.

| mode | recall@5 (95% CI) | mrr@5 | mean gold rank | vs full | vs off |
|---|---|---|---|---|---|
| memory_off | 0.00 [0.00, 0.39] | 0.00 | 6.00 | -0.83 | — |
| full | 0.83 [0.44, 0.97] | 0.83 | 1.83 | — | +0.83 |
| vector_only | 0.83 [0.44, 0.97] | 0.83 | 1.83 | +0.00 | +0.83 |
| no_decay | 1.00 [0.61, 1.00] | 0.89 | 1.33 | +0.17 | +1.00 |
| no_importance | 0.83 [0.44, 0.97] | 0.83 | 1.83 | +0.00 | +0.83 |
| no_mmr | 0.83 [0.44, 0.97] | 0.83 | 1.83 | +0.00 | +0.83 |
| no_rerank | 0.83 [0.44, 0.97] | 0.83 | 1.83 | +0.00 | +0.83 |

`no_rerank` isolates the hybrid relevance term that JEV replaces when a TypeSafe key is configured; see `docs/jev.md`.

**Reading:** recall@5 = fraction of queries whose gold fact made the top-5, with a 95% Wilson interval. mrr@5 = how early the gold fact appeared. mean gold rank = average position (6 means 'not in top-5').

**Verdict:** the spread between modes shows how much each component contributes. If `no_decay` ~= `full`, recency isn't earning its keep on this corpus; if `no_mmr` ~= `full`, the diversity step adds nothing; `vector_only` sets the baseline a deterministic pipeline must beat, and `memory_off` is the floor both of them clear.

**Honest caveat:** the default lane is a *precision* device — decay and importance deliberately demote old, low-importance facts below fresh, important ones. On this corpus the gold facts for old/trivial queries are exactly what the pipeline is designed to hide; that trade is the documented reason Iris has an *escalation lane* (design §4.5) for temporal/multi-hop questions, which searches daily notes directly instead of relying on the default lane.

**Misses per mode:**
- `memory_off`: which language does Omar prefer for systems programming, what instrument did Omar learn as a child, what food does Omar dislike, where does Omar's mother live, where does Omar's father live, when does Omar visit his parents
- `full`: what instrument did Omar learn as a child
- `vector_only`: what instrument did Omar learn as a child
- `no_decay`: none
- `no_importance`: what instrument did Omar learn as a child
- `no_mmr`: what instrument did Omar learn as a child
- `no_rerank`: what instrument did Omar learn as a child

## Escalation lane — does it close the gap?

Measured against the escalation lane itself, not against `memory_off`: the lane is a second retrieval path, so its own recall@5 is the number that matters.

| metric | value |
|---|---|
| escalation recall@5 | 1.00 |
| escalation mrr@5 | 0.89 |

The escalation lane (decay disabled, daily notes only) is triggered by temporal signals or a weak default lane, and it **recovers every query the default lane missed**:
- `what instrument did Omar learn as a child`

The two lanes together answer everything the default lane alone hides — the precision trade is now a *choice*, not a blind spot.

## Statistics (P8, audit G6)

A point estimate with no interval is not a measurement. Three things are reported here that were not before:

1. **An interval on every rate.** `recall@5` is a proportion over 6 queries, so it carries a Wilson interval — `6/6` is not certainty, and the interval says so.
2. **A pre-registered decision rule**, written down before the run (direction=increase, min_effect=0.2, alpha=0.05) and applied to the *paired* per-query outcomes so query difficulty cancels.
3. **A stated noise floor.** This lab is deterministic by construction (hash embeddings, no model calls), so run-to-run noise is **0** — two runs produce identical numbers. The uncertainty quoted here is *sampling* uncertainty from a small query set. Once a model is in the loop (a JEV rerank, a judge), repeat a configuration 3–5x and report `noise_floor()` instead; that is the number an effect must beat.

**Power.** Resolving Δ=0.2 at σ=0.25 needs **25 queries per arm**; this set has 6. So the rule is reported as `inconclusive` wherever it cannot clear the threshold — naming an underpowered set is a result, not a failure.

| mode | Δ recall@5 vs full | 95% CI | verdict |
|---|---|---|---|
| vector_only | +0.00 | [+0.00, +0.00] | inconclusive |
| no_decay | +0.17 | [+0.00, +0.50] | inconclusive |
| no_importance | +0.00 | [+0.00, +0.00] | inconclusive |
| no_mmr | +0.00 | [+0.00, +0.00] | inconclusive |
| no_rerank | +0.00 | [+0.00, +0.00] | inconclusive |

**Why `inconclusive` is the common answer at this size:** with six queries a single query is worth ~0.17 of recall — larger than any threshold worth pre-registering. The lab's value is the direction and the mechanism, not a p-value. Enlarging the query set is the prerequisite for a pass/fail claim.

### Does memory earn its keep? (the OFF baseline)

The one comparison that is meaningful at this set size, because the arms are maximally far apart rather than separated by a single knob:

| comparison | Δ recall@5 | 95% CI | verdict |
|---|---|---|---|
| `full` vs `memory_off` | +0.83 | [+0.50, +1.00] | pass |

A `pass` here means the interval clears the pre-registered +0.2 minimum effect: memory's contribution on this corpus is larger than the query set's own sampling noise. A `fail` would mean retrieval was actively hurting, which is the outcome the OFF arm exists to catch.

**Scope, stated plainly.** This is a *retrieval* OFF arm: the corpus is fixed and the question is which stored fact reaches the caller. It says nothing about the write path (capture, dreaming, consolidation), so it is not a whole-system memory-off reply-quality study — that comparison needs the capture node in the loop and a judged answer set, and it is the natural next study rather than something this file can claim. What it does establish is the floor: the number a memory change must beat before anyone may call it an improvement.