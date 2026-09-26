# Architecture

For someone who is about to change Iris. It maps where things are, what happens
on a turn, and which rules are load-bearing — the ones a plausible-looking edit
would break.

- **Want a recipe instead?** [`docs/extending.md`](extending.md) walks through
  adding a tool, a skill, a channel, a role, a guard and an eval metric.
- **Want the plan and the reasoning?** [`docs/blueprint.md`](blueprint.md) is the
  phase-by-phase design; [`docs/jev.md`](jev.md) is the judgment layer.

## The shape

```
             ┌──────────────────────────────────────────────────────┐
  CLI        │ iris/cli/          typer app: chat, skills, agents,  │
  (a client) │                    cron, tools, guards, doctor        │
             └───────────────┬──────────────────────────────────────┘
  Telegram   ┌───────────────▼──────────────────────────────────────┐
  (a client) │ mcp_servers/telegram/   MCP server → iris_ai.channels    │
             └───────────────┬──────────────────────────────────────┘
  HTTP       ┌───────────────▼──────────────────────────────────────┐
  (a client) │ iris/api.py        FastAPI; the only public surface   │
             └───────────────┬──────────────────────────────────────┘
             ┌───────────────▼──────────────────────────────────────┐
  Library    │ iris/engine.py     harness(): builds and owns         │
             │                    files · index · llm · jev · graph  │
             └───────────────┬──────────────────────────────────────┘
```

Everything above the library is a **client**. They share exactly one contract
(`iris_ai.channels.brain.BrainClient`): `respond`, `resume`, `stream`, `json_get`,
`json_post`. The CLI calls it in-process, the bridge calls it over HTTP, and both
get the same behaviour because there is only one turn pipeline behind it. If you
add a client, do not add a second pipeline.

## What happens on a turn

`ChatGraph` (`iris/agent/chat.py`) is a LangGraph `StateGraph` with seven nodes:

```
START → onboarding? → assemble_context → (compact) → agent ⇄ tools → journal → capture → END
```

| Node | Does | Cost |
|---|---|---|
| `onboarding` | first-run wizard (profile questions) | one cheap call per answer, once ever |
| `assemble_context` | bootstrap tiers (MEMORY.md, USER.md), skills block, recall | JEV #2 (skill suggestion) |
| `compact` | summarise history past the trigger budget | one strong call, only when over budget |
| `agent` | the ReAct loop; may call tools | the real reply |
| `tools` | dispatches tool calls **through the guard chain and the policy** | varies |
| `journal` | daily digest + the reflection pass | JEV #8, off the reply path by default |
| `capture` | extract a durable fact from the turn | JEV #7, gated by a deterministic prefilter |

Per-turn observation (`iris/turnlog.py`) is a `ContextVar`, not graph state: the
nodes inside one `ainvoke` share it and nothing needs a reducer to merge.

**Load-bearing order** inside `_tools`: the guard chain runs *before* dispatch
(`Guards.before` → `Guards.record` → `dispatch` → `Guards.after`). A refusal must
cost nothing, so it must happen before a provider is touched.

## Modules worth knowing

| Module | Responsible for | Not responsible for |
|---|---|---|
| `engine.py` | assembling the runtime, degraded mode, lifecycle | any policy decision |
| `agent/chat.py` | the turn pipeline and its node implementations | tool implementations |
| `agent/tools.py` | tool definitions, schemas, dispatch | which tools exist (see `toolpolicy`) |
| `toolpolicy.py` | tool **classes** → policy, visible-surface budget | enforcing policy (that is `dispatch`) |
| `guards.py` | the pre-tool chain: budget → circuit → spiral → context → record | budgets (see `budget.py`) |
| `budget.py` | scoped ceilings, counters split by kind, day persistence | who calls it (the chain) |
| `approval.py` | digest binding, replay guard, terminal-state guard, fail-closed | prompting the owner |
| `security.py` | bearer auth: the one definition of the header | who is allowed to do what |
| `redact.py` | credential scrubbing for anything written to disk | deciding what to log |
| `trace.py` / `turnlog.py` | the turn trace and its content policy | the decisions being recorded |
| `memory/` | files (source of truth), chunking, index, capture, dreaming, reflection | the shape of a turn |
| `jev/` | the judgment layer and every integration | acting on a judgment (call sites do) |
| `computer/` | the one screen-control tool: permission model, provider, audit | the kernel boundary (there isn't one) |
| `skills/` | discovery, manifests, `allowed-tools` policy, the script gate | writing skills |
| `agents/` | roles, the orchestrator, typed handoffs | the lead agent's own loop |
| `eval/` | statistics: intervals, power, agreement, decision rules | running the lab (`scripts/eval_lab.py`) |

## Invariants

These are asserted by tests. If you find yourself relaxing one, that is a design
conversation, not a test fix.

1. **Memory files are the source of truth; the index is derived.** Anything
   written must survive deleting Postgres, and `reindex_all()` must rebuild it.
2. **`deny` always wins** in tool policy, including across a class-wide deny
   re-opened by a per-tool `allow` (`tests/test_tool_policy.py`).
3. **Every tool declares a class**, asserted in *both* directions: no
   unclassified tool, and no declaration for a tool that does not exist.
4. **A guard can only refuse.** It cannot re-enable what another guard closed,
   and refusals happen pre-dispatch (`tests/test_guard_wiring.py`).
5. **Approval is bound to the action it showed.** The digest is of the *effective*
   arguments after edits; one `tool_call_id` grants once per thread; a
   side-effecting envelope with no digest fails closed.
6. **Nothing raises into a turn** from telemetry, reflection, capture, dreaming or
   the background pool. They degrade; they do not take the reply down.
7. **Secrets never reach disk and never reach the screen.** `iris doctor` prints
   key *names*; the trace records tool arguments as a hash (`iris/redact.py`).
8. **JEV is optional.** Every integration falls back to a deterministic path, and
   deleting `iris/jev` must leave Iris exactly as it was before it.
9. **CLI text is cp1252-clean.** A Windows console cannot encode a character
   outside it, and the failure is a traceback, not a missing glyph
   (`tests/test_cli.py`).
10. **Every setting is documented** in `.env.example`, and every key there names a
    real setting (`tests/test_packaging.py`).

## Where state lives

| State | Where | Survives restart? |
|---|---|---|
| Memory, dreams, skills, profile | `workspace/*.md`, `workspace/memory/` | yes — it *is* the product |
| Vector index, FTS, checkpoints (threads, pending approvals) | Postgres + pgvector | yes |
| Turn traces, cost ledger, hallucination flags, action log | `workspace/config/*.jsonl` | yes, rotated/bounded |
| Cron jobs | `workspace/config/tasks.json` | yes |
| Day token counters | `workspace/config/budget.json` | yes — that is the point |
| Guard spiral/circuit state | in-process, per run | no — and it should not |
| Approval replay guard | in-process, per thread | no; a restart already invalidates the interrupt |
| Per-turn observations | `ContextVar` | no |

## Reading the code

Start with `engine.harness()` — it is the only place wiring happens, so it shows
what depends on what. Then `agent/chat.py::_build` for the graph, and
`agent/tools.py::dispatch` for what a tool call actually goes through. Everything
else is a module with one job.
