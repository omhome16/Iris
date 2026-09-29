# 01 — Kernel & runtime

The kernel is the one piece that is *not* pluggable. It owns the shape of a turn,
the durability of a turn, and the order in which everything else is consulted. It
knows nothing about models, memory, transports or storage.

## 1. A turn as an explicit state machine

Today the loop is a LangGraph graph (`agent/chat.py`). The redesign makes the loop
explicit and small — a `Turn` driven through named steps, each of which is a
pure-ish function over `TurnState` with injected capabilities:

```
receive → assemble → plan? → act* → observe* → finalize → persist
             │          │        │        │
             │          │        └── tool call → policy → dispatch → result
             │          └── optional decomposition (plan-and-execute)
             └── system + memory + skills + tool schemas (stable prefix first)
```

`act*` repeats while the model emits tool calls, bounded by the budget and the
guard chain. Each iteration is a step the journal records.

Why explicit over a graph library: the loop is the product's differentiator and
its debuggability. A reader should be able to see the whole turn in one screen.
A graph library is excellent when the topology is the unknown; here the topology
is known and stable, and the interesting variation lives in the capabilities.

**Rejected:** keep LangGraph as the kernel. It couples durability, HITL and state
to a 900-line dependency and makes "read the whole turn" impossible. It returns as
an optional `Orchestrator` adapter (`iris_ai.orchestrators.langgraph`) for users
who want graph topologies; the native kernel is the default.

## 2. Durability without a framework

Agent work must survive a restart and a crash mid-tool. The design borrows the
settled durable-execution pattern (Temporal-style) without the server:

1. **Append-only journal.** Every step appends an event (turn start, model call
   with a hash of the prompt, tool call with a digest of args, tool result,
   approval request/decision, turn end) to `journal.jsonl`, per session.
2. **Deterministic replay.** On resume, replay the journal to reconstruct
   `TurnState`, then continue from the last step.
3. **Idempotent tool boundaries.** A tool that has a recorded result is not
   re-run on replay — the journal is the cache. Side-effecting tools carry an
   idempotency key.
4. **Durable approvals.** An approval is a journal event; a pending approval
   survives a restart and is bound to the digest of the action it showed.
5. **Versioned prompts and tool schemas.** The journal records the prompt/tool
   *version*, so a replay after a prompt edit is detected as divergent rather
   than silently different.

**Rejected:** checkpoint-only durability (LangGraph's model). A checkpoint is a
snapshot; a journal is a history. History is what makes "why did it do that?"
answerable and replay safe.

Recovery is tested by killing the process mid-tool in CI and asserting the turn
completes exactly once.

## 3. Orchestration modes

One kernel, three orchestration shapes selected by config (`[orchestrator] mode`):

| Mode | What it does | When |
|---|---|---|
| `react` (default) | interleaved reason/act | most turns |
| `plan-execute` | an up-front plan, re-planned on failure | long multi-step tasks |
| `delegate` | the lead spawns bounded specialists and merges typed handoffs | research/verification |

All three are implementations of the same `Orchestrator` interface, and all three
run *inside* the same kernel with the same guards, budgets and trace. Multi-agent
is a mode, not a second system — which is what stops a delegation layer from
growing its own invisible budget.

## 4. Context assembly

Assembly is deterministic and ordered so the prompt cache holds:

1. system prompt (versioned) — identity, rules, tool surface
2. memory tiers, each within its own token budget (stable order)
3. skill roster (name + description only — progressive disclosure)
4. history, with compaction triggered by a token ceiling
5. the current turn

Compaction flushes durable facts to the memory backend before trimming, so a
bounded context never loses what mattered. The stable prefix comes first so the
provider's prefix cache survives across turns.

## 5. Hooks, guards, budgets

The hook bus (`iris_ai.hooks`) is the kernel's extension seam, already shipped:
`turn_start`, `pre_tool`, `post_tool`, `on_error`, `turn_end`. The guard chain is
its first subscriber. Guards can only refuse; budgets are counters the guard chain
reads. Both are evaluated *before* dispatch, so a refusal never reaches a model or
a tool.

Deep-dive of the policy side: `05-safety.md`. Of the extension side: `06-extensibility.md`.

## 6. What this replaces

| Today | Redesign |
|---|---|
| `agent/chat.py::ChatGraph` | `kernel/turn.py` (native) + optional LangGraph adapter |
| Postgres checkpointer | append-only journal + replay (storage-agnostic) |
| `guards.py` inline | guard chain as a `Hook` (already migrated) |
| `agents/orchestrator.py` | `delegate` orchestrator mode |
| `budget.py` | `kernel/budget.py`, unchanged semantics |

The memory algorithms, JEV judgments and tool implementations are *called by* the
new kernel, not rewritten by it.
