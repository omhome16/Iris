# Iris — the manual

One document for the whole system. Every page of Iris is here: what it is, how to
install it, every CLI command and setting, how a turn actually runs, how memory
works, how the safety layer is wired, how to extend it without touching core, and
how to deploy and operate it.

If you read one section, read [§1](#1-what-iris-is) and then
[§2](#2-install-and-first-run). The rest is reference you can jump into.

**Contents**

| § | Page | What it answers |
|---|---|---|
| 1 | [What Iris is](#1-what-iris-is) | the shape of the thing, and what it deliberately is not |
| 2 | [Install and first run](#2-install-and-first-run) | clone to a reply, and what each failure means |
| 3 | [The CLI](#3-the-cli) | every command, flag and exit code |
| 4 | [Configuration](#4-configuration) | precedence, `.env`, `config/harness.toml`, `.mcp.json`, every knob |
| 5 | [The library](#5-the-library) | `harness()`, `BrainClient`, degraded mode |
| 6 | [Architecture](#6-architecture) | layers, modules, the turn, the invariants, where state lives |
| 7 | [Memory](#7-memory) | four tiers, provenance, capture, recall lanes, dreaming, forgetting |
| 8 | [The judgment layer](#8-the-judgment-layer) | JEV: the eight decisions, thresholds, fail-open, troubleshooting |
| 9 | [Safety](#9-safety) | guards, budgets, approvals, tool policy, secrets, sandboxes, residual risk |
| 10 | [Extending Iris](#10-extending-iris) | capability protocols, plugins, hooks, recipes |
| 11 | [Interfaces](#11-interfaces) | CLI, HTTP API, ACP, Telegram, channels and clients |
| 12 | [Observability and cost](#12-observability-and-cost) | traces, spans, the ledger, eval statistics |
| 13 | [Models and providers](#13-models-and-providers) | the provider table, two tiers, embeddings, retries |
| 14 | [Scheduling and proactivity](#14-scheduling-and-proactivity) | cron kinds, missed windows, nightly sleep, morning brief |
| 15 | [Deployment](#15-deployment) | hosting, volumes, secrets, backups, rollback, demo mode |
| 16 | [Testing and CI](#16-testing-and-ci) | what runs, what each job proves, the compatibility matrix |
| 17 | [Troubleshooting](#17-troubleshooting) | symptom → cause → fix, in one table |
| 18 | [Working on Iris](#18-working-on-iris) | house rules, conventions, where to start reading |

---

## 1. What Iris is

Iris is a **personal agent harness**: a library plus a CLI that gives a model
memory, judgment, tools and a durable turn — and that you configure into whatever
assistant you want instead of editing source to make her yours.

Four faces, one brain:

```
        library            `import iris_ai; async with iris_ai.harness() as brain:` — the product
        CLI                `iris chat`, `iris init`, `iris doctor`, … — the fastest way in
        HTTP API           POST /chat, /chat/stream, /chat/resume … — what clients call
        editor (ACP)       `iris-acp` — the same kernel, driven by Zed/JetBrains/…
```

Everything above the library is a **client**. They share one contract and one turn
pipeline, so memory, judgments, approvals, budgets and traces behave identically
whether you are in a terminal, an editor, or a Telegram chat. There is no second
brain to keep in sync.

### What it is, concretely

| Capability | What that means here |
|---|---|
| **Memory** | Markdown files are the source of truth (`MEMORY.md`, `USER.md`, daily notes, skills). The index is *derived and rebuildable*. Four tiers, four provenance levels, decay, MMR diversity, and consolidation by dreaming rather than by the write path |
| **Judgment** | A System One model (TypeSafe JEV) makes the decisions that are *questions over supplied text* — recall relevance, skill choice, injection screening, capture, sufficiency. Every one has a deterministic fallback and a latency budget |
| **Tools** | A declared tool surface with per-class policy, a visible-surface budget, MCP servers as configuration, and a pre-dispatch guard chain that refuses *before* anything is spent |
| **Durability** | An append-only turn journal gives exactly-once tool boundaries and approvals that survive a restart |
| **Safety** | Policy layer above the model: digest-bound approvals, an injection screen on untrusted content, a secret store, and a script sandbox that fails closed |
| **Observability** | Per-turn traces with stage timings, a cost ledger for every model call, and optional OTel GenAI spans — local JSONL by default, no collector needed |
| **Extensibility** | Capability Protocols with registries: models, memory, judges, channels, tools, hooks, secret stores. Plug-and-play by entry point, not by forking |
| **Zero-service default** | SQLite for memory and threads. No Postgres, no Docker, no daemon before it will say hello |

### What it deliberately is not

- **Not a hosted product.** Single-user, local or self-hosted, one owner. There is
  no multi-tenancy, no per-user auth model, no billing.
- **Not a chat wrapper.** The interesting parts are the memory write path, the
  judgment layer and the policy boundary — not the streaming loop.
- **Not serverless.** Telegram long-polling and the scheduler hold state that a
  scale-to-zero host tears down between turns. See [§15](#15-deployment).
- **Not a multi-agent swarm.** Two specialists, both bounded, both opt-in per
  turn, both read-only. Multi-agent systems use roughly 15× the tokens of a chat,
  so an unbounded delegation layer is a cost bug with a nice name.
- **Not a fork of core to customize it.** If you are editing `src/` to add a
  capability, something is missing from [§10](#10-extending-iris) — file an issue
  instead.

### Identity, so it never surprises you

| Thing | Value |
|---|---|
| Distribution | `iris-personal-ai` |
| Import | `iris_ai` |
| CLI | `iris` |
| Editor process | `iris-acp` |
| Version | single-sourced in `src/iris_ai/__init__.py`, read by hatchling |
| Language | Python **only**, 3.12 and 3.13 |
| Licence | MIT |

---

## 2. Install and first run

### Requirements

| | |
|---|---|
| Python | **3.12 or 3.13** |
| [uv](https://docs.astral.sh/uv/) | for a checkout (`pip install uv`) |
| A provider key | **optional to start.** Without one, recall is keyword-only and model calls degrade — the harness still boots and answers deterministically |
| Postgres | **not needed.** The default memory backend is one SQLite file; threads fall back to a second one |
| Docker | **not needed.** Only for Postgres at scale, the full API + bridge stack, or the `container` script sandbox |

The five-minute claim is measured, not asserted: CI job `onboarding` runs the
install-and-init half on a clean checkout, asserts a reply with `--offline` (no
provider key and **no Postgres service in that job**), times the whole thing, and
fails past 300 seconds.

### The path

```bash
git clone https://github.com/omhome16/Iris.git && cd Iris
uv sync                      # install from the lockfile
uv run iris init             # write .env + config/harness.toml, then prove it
uv run iris chat             # talk to it
```

Then put one provider key in the `.env` that `iris init` wrote, and start the next
session with it. That is the whole setup.

Installing as a package instead:

```bash
pip install iris-personal-ai           # core
pip install "iris-personal-ai[acp]"    # + the editor adapter
pip install "iris-personal-ai[otel]"   # + OTLP span export
```

### What `iris init` actually does

It writes files, then **measures** the result rather than assuming it.

| File | |
|---|---|
| `.env` | copied from `.env.example`; **never overwritten** without `--force` |
| `config/harness.toml` | copied from the example — the declarative manifest a boot reads |
| `workspace/AGENTS.md` | the neutral operating contract, written only if the workspace has none |
| `workspace/README.md` | what each file in a workspace is for |

| Probe | What it proves |
|---|---|
| one no-op completion on the cheap tier | a provider key that works, reported with its latency |
| the configured memory store opened for `stats()` | the no-service claim holds, and the chunk count is real |
| the real thread store walked | which tier a conversation would land in (`postgres` → `sqlite` → in-memory, with a warning when it is the last one) |

Recall is reported separately and honestly: with no embedding provider the report
says **keyword-only**, names the fix (`GEMINI_API_KEY`, the default embedding
model) and the keyless alternative (`LLM_PROVIDER=ollama` with
`ollama/nomic-embed-text`). A working install that under-reports is worse than a
warning — so it is a warning.

`iris init` does not ask questions. `--yes` writes a blank profile for CI.
The profile screen is `iris config` (owner, assistant name, tone, timezone,
sleep hour, persona). `--offline` skips the two live probes (CI and
pre-commit use it); `iris doctor` re-checks the environment any time.

It is also safe to re-run: it reports `kept (already exists)` rather than
replacing anything you have edited, and `--force` covers `.env` and the manifest
only — never your `AGENTS.md`.

### Talk to it

```bash
uv run iris chat                          # streaming REPL; same pipeline as the API
uv run iris chat --once "summarize my notes"
uv run iris chat --session work           # a separate thread (memory continuity)
uv run iris chat --no-banner              # skip the start-screen art
```

First contact does not ask questions in the chat. Set the profile with
`iris config` (owner, assistant name, tone, timezone, sleep hour, persona).
That writes `workspace/USER.md`, `workspace/PERSONA.md`, and
`workspace/config/iris.json`. A fresh workspace has no name until that screen
is saved. `iris chat` on a real terminal is the full-screen UI; `--once` and
a pipe stay plain text.

### Verify the pieces yourself

```bash
uv run iris doctor        # environment: providers, store, recall, threads, secret store
uv run iris plugins       # which channels, tools, hooks and MCP servers are live
uv run iris costs         # what the model calls cost, from the ledger
uv run iris guards        # the ceilings, on the record
uv run pytest tests -q    # the suite (the two Postgres files fail loudly without a DB)
uv run ruff check .       # lint is a gate, not a suggestion
```

### When it does not work

| Symptom | Cause and fix |
|---|---|
| `recall: keyword-only` | no embedding provider. Either a key, or `LLM_PROVIDER=ollama` with a local model |
| `model check: fail` | no usable provider key (or `LLM_PROVIDER` names one you have no key for). `iris doctor` prints the key **names** it found |
| `threads: in-memory` | neither Postgres nor the SQLite path was usable; threads will not survive exit. Check `CHECKPOINTER_PATH` is writable |
| `model not found` / provider errors on a first turn | the model name in `.env` does not exist for that provider; `iris doctor` lists the resolved provider |
| Commands exit non-zero after `init` | the report's last line is the reason; `1` means a `fail`, not a `warn` |

The full symptom table is [§17](#17-troubleshooting).

---

## 3. The CLI

`iris` is a client of the library, never a second implementation of it. Two rules
shape every command:

- **Read-only inspection works with no engine running.** `iris tools`, `iris
  policy`, `iris plugins`, `iris guards`, `iris cron list`, `iris skills` and
  `iris agents` read declarations and files, so they answer questions on a machine
  where nothing is running.
- **Diagnostics are portable.** Every character a command prints is cp1252-safe,
  and the plain-text form is the contract — `iris doctor > doctor.txt` is readable
  and encodable on a Windows console. Colour and the rounded frames are extra, and
  they are only drawn on a terminal.

Run `iris` with no arguments, or `iris --help`, for the start screen: the help is
**generated from the command registry**, so a command that exists is a command
that is listed.

### 3.1 Start here

| Command | What it does |
|---|---|
| `iris init [--config PATH] [--force] [--offline]` | write `.env` + the manifest, seed a neutral workspace, then probe model / memory / threads / recall |
| `iris chat [--session ID] [--once TEXT] [--no-banner]` | streaming REPL on the same turn pipeline as the API |
| `iris doctor` | offline environment checks; names only, never secret values; exits 1 on any `fail` |
| `iris version` | version, interpreter, install location |

Inside `iris chat`: `/exit` or `/quit` leaves, `/help` lists the two. Anything
else is a turn. Degraded mode is announced on **stderr** so `iris chat --once >
out.txt` stays clean.

### 3.2 Configure

| Command | What it does |
|---|---|
| `iris secrets [backend\|list\|set\|rm] [NAME] [VALUE]` | where a token lives, which `${VAR}`s are missing, and store or remove one. Names, locations and `set`/`missing` only — **never a value**. `set` without a value prompts with hidden input |
| `iris mcp [list\|add\|remove\|test] [NAME] [--url\|--command] [--args] [--trust] [--approval] [--enabled/--disabled]` | declare MCP servers without hand-editing JSON. Validates through the same parser the boot uses before saving, and defaults to `--trust untrusted` |
| `iris policy [show\|classes\|overrides\|servers]` | what every tool and server is allowed to do, and where each decision came from |

`iris mcp add` never sets trust implicitly. A CLI that defaulted to `owner` would
be handing away the trust model for convenience.

### 3.3 Inspect

| Command | What it does |
|---|---|
| `iris tools [policy\|actions] [--limit N]` | every declared tool with its class, resolved policy, where the decision came from, its namespace and whether it is on the visible surface — plus the verbatim catalog the prompt gets for the deferred remainder. `actions` reads the computer-use audit log |
| `iris plugins [channels\|tools\|hooks\|mcp] [--live]` | what is registered, whether config enabled it, and where each came from. `hooks` attaches installed plugins to a throwaway bus so you can see what would subscribe. `mcp --live` connects the declared servers |
| `iris guards [--json]` | the guard chain's declared policy and today's spend, read from `config/budget.json`. Needs no engine |
| `iris costs [summary\|daily\|weekly] [--days N]` | every model call's cost from the append-only ledger, with cache-hit context |
| `iris agents [roles\|show NAME\|handoffs] [--limit N]` | the multi-agent pack with each role's bounds, one role's full prompt, and recent delegations read back out of the traces |
| `iris skills [list\|show NAME\|validate\|approve NAME]` | every skill from every source with its score and tools; `validate` **exits 1 on any error-level issue** so it works as a gate; `approve` re-pins a third-party manifest whose digest changed |
| `iris cron [list\|add\|rm ID] [--once\|--every\|--at] [--instruction TEXT]` | time-triggered work, readable and writable offline |

### 3.4 Maintain

| Command | What it does |
|---|---|
| `iris migrate [--to sqlite\|pgvector] [--dry-run]` | move the memory index to another store, and point `.env` at it |

The migration is a **rebuild, not a copy**: the Markdown is re-read through the
same `Reindexer` a normal boot uses. That cannot drift, works when the old store is
already gone (the usual reason to migrate), and means `--dry-run` can answer
"what would happen" without creating anything.

### 3.5 Exit codes

| Code | Meaning |
|---|---|
| `0` | success, or success with warnings (`warn` never fails a command) |
| `1` | a `fail`-level check, an unreachable store, a refusal, or a crash (with `--debug` you get a traceback instead) |
| `2` | usage error: an unknown action, or a missing name for `show`/`add`/`rm` |

`--debug` (or `IRIS_DEBUG=1`) re-raises the original exception so you get a real
traceback. Without it, every command prints `error <what>`, then a hint.

---

## 4. Configuration

### 4.1 Precedence

```
defaults  <  .env  <  config/harness.toml  <  real environment  <  CLI flag
```

A **real** environment variable always wins, so a container or CI override cannot
be defeated by a file. The manifest wins over `.env`, because `.env` is where a
copied sample's defaults live and `WORKSPACE_DIR=./workspace` there must not
silently defeat a profile that names its own workspace.

**Secrets belong in `.env` or the secret store, never in the manifest.** The
manifest is safe to commit; `.env` is not.

### 4.2 The files

| File | Committed? | What it is |
|---|---|---|
| `.env` | **no** | secrets and machine-local overrides; created by `iris init` from `.env.example` |
| `.env.example` | yes | the documented sample. A test asserts every setting appears here and every key here names a real setting |
| `config/harness.toml` | yes | the declarative manifest: channels, capability backends, ordinary settings. Unknown keys are logged and ignored, so a typo is visible rather than silent |
| `config/mcp.json.example` → `.mcp.json` | example yes, live no | declared MCP servers (the ecosystem's file shape, so a config you already have works) |
| `workspace/config/*` | **no** | runtime state: threads, budget counters, the ledger, traces, backups |

`HARNESS_CONFIG` points a boot at a different manifest, which is how a profile
ships its own:

```bash
HARNESS_CONFIG=examples/assistant/harness.toml uv run iris init
```

### 4.3 A manifest, annotated

```toml
[channels]
enabled = ["telegram"]     # empty means "every registered channel"
disabled = []              # deny always wins

# model_backend  = "litellm"    # ModelBackend: `litellm` ships in core
# memory_backend = "sqlite"     # MemoryBackend: `sqlite` (default), `pgvector`, `null`
# judge_backend  = "jev"        # Judge: `jev` ships in core

# llm_provider = "auto"
# tool_guard_enabled = true
# tool_max_calls_per_turn = 8
# computer_enabled = false
# iris_timezone = "UTC"
# mcp_servers_file = ".mcp.json"
```

Every key is the lowercase name of a setting. Anything you would put in `.env`
can live here instead, except secrets.

### 4.4 The settings that matter most

**Where things live**

| Setting | Default | Meaning |
|---|---|---|
| `WORKSPACE_DIR` | `./workspace` | the memory workspace — this *is* the product, back it up |
| `SANDBOX_DIR` | `./workspace/sandbox` | the only place the file tools may write |
| `SQLITE_PATH` | `./config/memory.db` | the default memory index, one file |
| `CHECKPOINTER_PATH` | `./config/checkpoints.db` | threads and pending approvals |
| `MEMORY_BACKEND` | `sqlite` | `sqlite`, `pgvector`, `null`, or a registered plugin |
| `POSTGRES_DSN` | — | only read when `pgvector` is selected |
| `HARNESS_CONFIG` | `config/harness.toml` | the manifest a boot applies |
| `MCP_SERVERS_FILE` | `.mcp.json` | declared external servers |

**Model and judgment**

| Setting | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `auto` | which provider key to prefer; `auto` takes the first one present |
| `*_API_KEY` | — | one provider key is enough to start |
| `*_STRONG_MODEL` / `*_CHEAP_MODEL` | per provider | two tiers; a provider with no pinned id ships empty and is *skipped*, never attempted with a guess |
| `TYPESAFE_API_KEY` | — | empty disables the judgment layer; every integration falls back |
| `JUDGE_BACKEND` | `jev` | `jev`, or a registered plugin judge |

**Safety and cost**

| Setting | Default | Meaning |
|---|---|---|
| `TOOL_GUARD_ENABLED` | `true` | `false` turns the whole pre-dispatch chain into a no-op |
| `TOOL_MAX_CALLS_PER_TURN` | 8 | growth ceiling for tool calls in one turn |
| `TOOL_FAILURE_THRESHOLD` | 2 | consecutive failures of one tool that open its circuit |
| `TOOL_SPIRAL_MIN_REPEATS` / `TOOL_SPIRAL_JACCARD` | 3 / 0.72 | the same tool with near-identical arguments, and what counts as "the same" |
| `TOOL_POLICY_OVERRIDES` | — | `"send_message=deny,external=deny"` — may only *tighten* |
| `BUDGET_MAX_TOKENS_PER_TURN` / `_PER_DAY` | 0 | token ceilings. **`0` means no ceiling**, not "a limit of zero" |
| `SECRET_STORE` | `auto` | `env` (read-only), `keyring`, `file` (0600, *not* encrypted) |
| `EXEC_SANDBOX` | `process` | `process` or `container`; `container` **fails closed** when Docker is absent |
| `APPROVAL_BIND_DIGEST` / `APPROVAL_GUARD_REPLAY` | `true` | bind a resume to the action it showed; let one `tool_call_id` grant once per thread |
| `COMPUTER_ENABLED` | `false` | off means the `computer` tool is not registered at all |

**Context and memory behaviour**

| Setting | Default | Meaning |
|---|---|---|
| `BOOTSTRAP_BUDGET_TOKENS` | 4000 | how much of `MEMORY.md` enters the prompt |
| `USER_PROFILE_BUDGET_TOKENS` | 1500 | how much of `USER.md` enters the prompt |
| `CONTEXT_COMPACT_TRIGGER_TOKENS` | 12000 | history size that triggers a compaction turn |
| `CONTEXT_KEEP_TOKENS` | 2000 | what survives compaction |
| `CONTEXTUAL_CHUNKING_ENABLED` | `true` | a cheap-model header per chunk before embedding, cached by content hash |
| `IRIS_TRACE_CONTENT` | `metadata` | `metadata`, `redacted`, `sampled` or `full`. **Credentials never reach the trace in any mode** |
| `IRIS_REFLECTION_BACKGROUND` | `1` | `0` runs the reflection pass inline instead of off the reply path |
| `IRI_S_TIMEZONE` (`IRIS_TIMEZONE`) | `UTC` | drives the sleep hour, the morning brief and every daily-note timestamp |
| `IRIS_API_TOKEN` | — | protect every API route except `/health`; boot logs a warning when unset |

There are more — `.env.example` is the complete, tested list, and
`iris doctor` reports the ones that resolve weirdly.

---

## 5. The library

The library is the product; the CLI and the API are faces on it.

```python
import iris_ai

async with iris_ai.harness() as brain:
    reply = await brain.respond("what did I decide about the Berlin budget?")
    print(reply)

    async for kind, payload in brain.stream("and the Lisbon trip?"):
        ...  # "custom" events: thinking, text deltas, tool calls, approvals
```

`harness()` is the only place wiring happens. It builds, in order: the manifest
application, the workspace, the memory backend through its registry, the model
backend through its registry, the judge, the tool surface, the guard chain, the
hook bus (with plugin hooks discovered and attached), the MCP pool, the
thread store, the journal, and — only when asked — the scheduler and
channels.

| Argument | Effect |
|---|---|
| `services=False` | do not start the scheduler or connect channels (what `iris chat` uses: the CLI is a client, not a service) |
| `postgres="auto"` | walk the thread-store ladder instead of requiring a database |

### 5.1 Interface-agnostic clients

A client implements `iris_ai.channels.brain.BrainClient` — `respond`, `resume`,
`stream`, `json_get`, `json_post` — or reuses `HttpBrainClient` and points it at a
core. Do not write a second turn pipeline: `mcp_servers/telegram/` is the worked
example over HTTP, `src/iris_ai/cli/chat.py` is the worked example in-process.

**Idempotency is the client's job.** If your transport can deliver a message twice
(Telegram does), deduplicate before you call `respond` or the owner's memory gets
the same fact twice — the bridge keeps an `UpdateLedger` for exactly this.

### 5.2 Degraded mode

Iris prefers to boot and under-report over refusing to start:

| Situation | What happens |
|---|---|
| No provider key | deterministic paths still run; model-backed features degrade. `iris doctor` names the missing keys |
| No embedding provider | recall is **keyword-only**, and the report says so instead of looking broken |
| Postgres unreachable | memory degrades to `null` (loudly: `mode`/`degraded_reason` say why), threads fall to SQLite |
| No thread store available at all | in-memory threads, with a warning that they will not survive exit |
| JEV unavailable | every judgment falls back deterministically, and the trace records that nothing was checked |
| An MCP server down | skipped, retried in the background; the session continues |
| A channel down | skipped, never fatal |
| Hosting the API | `postgres="require"` makes an unreachable store a **refusal** instead of a silent degradation |

---

## 6. Architecture

### 6.1 The shape

```
   CLI (client)          Telegram (client)         HTTP (client)        Editor (client)
        │                       │                        │                    │
        └──────────────┬────────┴───────────┬────────────┴───────────┬────────┘
                       ▼                    ▼                        ▼
                 iris/cli/          mcp_servers/telegram      iris/api.py         iris-acp
                       │              (MCP 2.0 client)        (FastAPI)      (Agent Client Protocol)
                       └────────────────────┬────────────────────┘
                                            ▼
                                 iris/engine.py :: harness()
                       builds and owns: workspace · memory · model · judge
                                        · tools · guards · hooks
                                        · MCP pool · thread store · journal
                                            │
                                            ▼
                                 iris/agent/chat.py :: ChatGraph
                       the turn: onboarding → assemble → agent ⇄ tools → journal → capture
```

Everything above `harness()` is a client. Everything below it is a module with one
job.

### 6.2 What happens on a turn

The turn is a native loop in `iris_ai.kernel.loop` (`TurnLoop` / `NativeGraph`).
`ChatGraph` still owns the nodes. There is no graph framework in the dependency list.

```
START → profile? → assemble_context → (compact) → agent ⇄ tools → journal → capture → END
```

| Node | Does | Cost |
|---|---|---|
| `onboarding` | asks the owner to run `iris init` when no profile exists | none |
| `assemble_context` | bootstrap tiers (`MEMORY.md`, `USER.md`) and the skills block. It does not search the index; the model recalls with the `memory_search` tool | one judgment (skill suggestion) |
| `compact` | summarise history past the trigger budget | one strong call, only when over budget |
| `agent` | the ReAct loop; may call tools | the real reply |
| `tools` | dispatches tool calls **through the guard chain and the policy** | varies |
| `journal` | daily digest + the reflection pass | one judgment, off the reply path by default |
| `capture` | extract a durable fact from the turn | one judgment, gated by a deterministic prefilter |

Per-turn observation (`iris/turnlog.py`) is a `ContextVar`, not graph state: the
nodes inside one `ainvoke` share it, and nothing needs a reducer to merge.

**The load-bearing order** inside `_tools`: the guard chain runs *before* dispatch
(`Guards.before` → `Guards.record` → `dispatch` → `Guards.after`). A refusal must
cost nothing, so it must happen before a provider is touched.

Every turn is bounded by a 120 s budget and a recursion cap, so a provider hiccup
cannot hang you — you get a graceful "still thinking" instead. When a streaming
client goes away, the turn is cancelled rather than left running.

### 6.3 Modules worth knowing

| Module | Responsible for | Not responsible for |
|---|---|---|
| `engine.py` | assembling the runtime, degraded mode, lifecycle | any policy decision |
| `agent/chat.py` | the turn pipeline and its nodes | tool implementations |
| `agent/tools.py` | tool definitions, schemas, dispatch, loading a deferred schema | which tools exist (`toolpolicy`) |
| `toolpolicy.py` | tool **classes** → policy, namespaces, the deferred catalog, the surface budget | enforcing policy (that is `dispatch`) |
| `guards.py` | the pre-tool chain | budgets (`budget.py`) |
| `budget.py` | scoped ceilings, counters split by kind, day persistence | who calls it |
| `approval.py` | digest binding, replay guard, terminal-state guard, fail-closed | prompting the owner |
| `security.py` | bearer auth: the one definition of the header | who is allowed to do what |
| `redact.py` | credential scrubbing for anything written to disk | deciding what to log |
| `trace.py` / `turnlog.py` | the turn trace and its content policy | the decisions being recorded |
| `kernel/` | the turn loop, the thread store, the journal, and the exactly-once tool boundary | which store implementation is configured |
| `registry.py` | names, sources, conflicts, entry-point discovery | what a plugin does |
| `hooks.py` | the lifecycle bus and its priority order | built-in policy (guards subscribe) |
| `manifest.py` | reading and applying `harness.toml`, and env precedence | what the settings mean |
| `capabilities/` | the Protocols and registries (models, memory, judges) | implementations |
| `memory/` | files (source of truth), chunking, index, capture, dreaming, forgetting, reflection | the shape of a turn |
| `jev/` | the judgment layer and every integration | acting on a judgment (call sites do) |
| `mcp/` | declared servers, trust, the pool, reconnect | the transport protocol (the SDK does) |
| `secrets/` | where a secret lives, and the `${VAR}` resolver | what a secret is worth |
| `skills/` | discovery, manifests, `allowed-tools` policy, the script gate | writing skills |
| `agents/` | roles, the orchestrator, typed handoffs | the lead agent's own loop |
| `interfaces/acp/` | the editor adapter: sessions, prompts, permissions | the kernel |
| `observability/` | shaping GenAI spans from events, the optional sink | sending them when unused |
| `eval/` | statistics: intervals, power, agreement, decision rules | running the lab (`scripts/eval_lab.py`) |

### 6.4 The invariants

These are asserted by tests. Relaxing one is a design conversation, not a test fix.

1. **Memory files are the source of truth; the index is derived.** Anything written
   must survive deleting the database, and a reindex must rebuild it.
2. **`deny` always wins** in tool policy, including a class-wide deny re-opened by a
   per-tool `allow`.
3. **Every tool declares a class**, asserted in *both* directions: no unclassified
   tool, and no declaration for a tool that does not exist.
4. **The namespace table partitions the declarations**, so the deferred catalog
   names every hidden tool — a group nobody belongs to would drop a capability from
   the prompt as well as from the surface.
5. **Loading a deferred tool can only add.** It never widens permission and never
   disturbs the visible head, so the provider's cached prefix survives.
6. **A guard can only refuse.** It cannot re-enable what another guard closed, and
   refusals happen pre-dispatch.
7. **Approval is bound to the action it showed** — the digest is of the *effective*
   arguments after edits; one `tool_call_id` grants once per thread; a
   side-effecting envelope with no digest fails closed.
8. **Nothing raises into a turn** from telemetry, reflection, capture, dreaming or
   the background pool. They degrade; they do not take the reply down.
9. **Secrets never reach disk and never reach the screen.** `iris doctor` prints
   key *names*; traces record tool arguments as a hash.
10. **JEV is optional.** Every integration falls back to a deterministic path, and
    deleting `iris_ai/jev` must leave Iris exactly as it was before it.
11. **CLI text is cp1252-clean**, asserted over the literals *and* over captured
    command output — a character outside it is a traceback on a Windows console,
    not a missing glyph.
12. **Every setting is documented** in `.env.example`, and every key there names a
    real setting.

### 6.5 Where state lives

| State | Where | Survives restart? |
|---|---|---|
| Memory, dreams, skills, profile | `workspace/*.md`, `workspace/memory/` | yes — it *is* the product |
| Vector index, FTS, checkpoints (threads, pending approvals) | SQLite by default; Postgres + pgvector optionally | yes |
| The turn journal (tool starts/ends, approvals, prompt version) | `workspace/config/journal.jsonl` | yes — it is what makes replay safe |
| Turn traces, cost ledger, hallucination flags, action log | `workspace/config/*.jsonl` | yes, rotated/bounded |
| Cron jobs | `workspace/config/tasks.json` | yes |
| Day token counters | `workspace/config/budget.json` | yes — that is the point |
| Guard spiral/circuit state | in-process, per run | no — and it should not |
| Approval replay guard | in-process, per thread | no; a restart already invalidates the interrupt |
| Per-turn observations | `ContextVar` | no |

---

## 7. Memory

One rule explains most of it:

> **The Markdown is the source of truth; the index is derived and rebuildable.**

Anything that cannot survive deleting the database is a cache, not a memory.

### 7.1 The four tiers, and provenance

| Tier | Artifact | Lifetime |
|---|---|---|
| working | the conversation, compacted | a session |
| episodic | `memory/YYYY-MM-DD.md`, dreams | decaying |
| semantic | `MEMORY.md`, `USER.md` (curated) | evergreen |
| procedural | skills (`SKILL.md`) | until revised |
| judgments | recall feedback, importance | signals over the above |

Four provenances gate everything: **owner** (you wrote it), **agent** (her own
pipelines), **untrusted** (imports, web pages — recallable, never promotable),
**system** (operational logs — never injected). Curated content may only graduate
from owner/agent sources, and the check is structural rather than a prompt rule.

### 7.2 What a workspace looks like

```
workspace/
├── AGENTS.md              # operating contract - survives every reset
├── USER.md                # your profile (stable preferences, relationships)
├── MEMORY.md              # curated long-term memory (evergreen facts)
├── DREAMS.md              # dream diary - read-only for Iris
├── memory/YYYY-MM-DD.md   # episodic daily notes (decay over time)
├── skills/*.md            # procedural skills she writes herself
├── imports/*.md           # ingested URLs (UNTRUSTED origin)
├── sandbox/               # the only place the file tools may write
├── config/iris.json       # onboarding state / identity
├── config/tasks.json      # scheduled jobs
├── config/traces.jsonl    # per-turn traces
├── config/llm_calls.jsonl # cost ledger (every model call)
├── config/budget.json     # today's counters, split by kind
├── config/journal.jsonl   # the turn journal (replay + durable approvals)
└── memory/.dreams/        # staging, contextual-chunk cache, recall feedback
```

### 7.3 The write path

Nothing reaches curated memory directly. A turn writes at most an **add-only daily
note line**, and promotion happens only in the dream cycle.

```
reply → capture prefilter (deterministic, free)
          │  first-person + durability cues, length floor  → most turns stop here
          ▼
      one judgment (JEV: Noul x2 + Score, or one cheap-tier JSON call)
          │  "is this a new durable fact, already in context, and how important?"
          ▼
      an ADD-ONLY line in memory/YYYY-MM-DD.md, stamped (note), agent provenance
          │
          ▼
      the Light-phase gate (deterministic score)  → dreaming may promote it to MEMORY.md
```

Three things this buys, all of them measurable in hindsight:

- **Cost control.** The prefilter means "ok"/"thanks" turns cost zero model calls.
- **Volume without unreliability.** Leaving capture to the agent's own `note` tool
  was measured and failed: 0 `note` calls across 36 traced turns, so `MEMORY.md`
  only ever grew from compaction flush.
- **No recall loop.** The judgment is shown the same assembled context the model
  sees and asked *"is this already in it?"*, so a fact recalled a hundred times
  still enters the daily note once.

The owner's own words supply the fact: JEV emits probabilities, not prose.

### 7.4 Chunking and embeddings

- **Contextual chunk headers** — before embedding, each chunk gets a cheap-model
  header (≤60 tokens) explaining its surrounding document, so vectors carry
  document-level meaning. Cached per file by content hash; plain chunks are the
  automatic fallback.
- **Deterministic prefilter before any capture judgment**, so nothing spends a
  model call to decide whether to spend a model call.
- **Embeddings fall back, in order:** a provider embedding API if a key exists
  (best quality) → a local Ollama model (`ollama/nomic-embed-text`) → **no
  embeddings at all**, which is FTS/BM25 keyword recall that still works and says
  so. That last rung is a supported mode, not a failure.

### 7.5 Recall lanes

| Lane | What it is | When |
|---|---|---|
| **default** | vector + FTS shortlist, **reranked by a judgment** (one request, one probability per candidate), then × recency decay × importance, then MMR for diversity | most turns |
| **escalation** | decay disabled, daily notes only | temporal questions ("when did…", "last month") or a weak default lane. Recovers old facts the default lane deliberately hides |
| **subagent** (`deep_dive`) | a research role with its own context window and tools, for iterative retrieval | when the answer is not obvious |

Recall **policy** (decay, importance, lane selection, MMR) lives above the store,
because it is policy rather than storage. A backend answers "which chunks are near
this query"; the kernel decides what that means for this turn. It also means the
weighting stays inspectable: relevance, decay and the importance multiplier are
carried separately on every hit, so the arithmetic is testable without a model.

### 7.6 Dreaming (consolidation)

The `/sleep` command — or the 04:00 job — runs **Light → REM → Deep**:

| Phase | Does |
|---|---|
| **Light** | scores staged signals on five weighted axes: occurrence, importance, richness, trigger-diversity and **recall feedback** (how often a memory was actually retrieved). Deterministic |
| **REM** | de-duplicates promoted candidates against existing curated memory |
| **Deep** | writes consolidated facts into `MEMORY.md`, retiring superseded entries rather than deleting them |

Staging is deliberately tainted: staged candidates are not curated memory, and
curated memory may only be written by this pass.

### 7.7 Forgetting

Nothing is silently deleted.

- **Retention** computes how much of each memory survived decay; entries below the
  rot threshold are flagged and surfaced for a decision (`iris chat`'s `/rot`,
  `/retention` over Telegram, or `GET /rot`).
- **`forget` is a two-phase gate** built on the graph's interrupt: asking her to
  forget pauses the turn and raises an approval. The memory is superseded **only
after you approve**; a cancel resumes the thread with the memory intact.
- **Daily notes are append-only.** Supersession marks an entry, it does not rewrite
  history.

### 7.8 Backends

| Backend | Embeddings | Ships as | Use |
|---|---|---|---|
| **SQLite** | local model or provider API | **default** | laptop, zero services, one file |
| Postgres + pgvector | provider API | optional | large corpora, existing installs |
| null | none | always | tests and degraded mode — it **raises** rather than returning an empty result set, so "recall is broken" cannot look like "nothing matched" |
| external vector service | provider | plugin | power users (`iris_ai.memory` entry point) |

The SQLite backend runs **FTS5 keyword search plus brute-force exact cosine over
the stored rows** (computed in numpy), rather than a native vector extension. That
is a deliberate deviation from the original design, which named `sqlite-vec`: the
zero-service default must not need a build step, and for a personal corpus
(10^4 chunks ≈ 60 MB of float32) an exact scan is fast enough. Approximate search
at scale is what `pgvector` is for.

Degradation is **per query and recoverable**, never silent: if the embedder has
failed, the query falls back to keyword-only (a worse answer, not a broken one),
a failed embed probe is suppressed for a cooldown before retrying, and `stats()`
distinguishes "no embedding provider" from "nothing indexed yet" instead of
guessing.

### 7.9 Moving stores

```bash
iris migrate --to sqlite      # from pgvector, or back again
iris migrate --dry-run        # what it would change, touching nothing
iris migrate --to null        # refused: the degraded stand-in stores nothing
```

See [§3.4](#34-maintain). The Markdown is re-read through the same reindexer a
normal boot uses, so a migration cannot produce a shape a boot would not.

---

## 8. The judgment layer

Iris's memory decisions used to be hand-tuned arithmetic and substring tests.
Those are proxies for judgments a System One model makes directly, and two of them
were measurably wrong: the hybrid recall score was scoring **0.83 recall@5 against
1.00 for plain cosine**, and skill selection was a casefolded substring match.

### 8.1 What JEV is

TypeSafe's **Jev** takes a *state* (text or JSON) plus one or more **typed
questions** and returns **typed answers with calibrated probabilities**. It does
not generate prose, code or explanations.

| Primitive | Question shape | Answer |
|---|---|---|
| **Noul** | "is this true?" | `noul` ∈ [0, 1] — the probability of *yes*, with no separate confidence |
| **Choice** | "which of these options?" | `choice` + per-option probabilities + `confidence` |
| **Score** | "where on this ordered scale?" | `score` (may sit between levels) + a legend + probabilities |

Properties that make it a *programming primitive* rather than a prompt:

- **Answers are constrained to the options supplied.** No prose to parse, no
  "the model wrapped JSON in markdown" recovery path.
- **Questions are independent and evaluated in parallel, in one request** — which
  is what makes one-request reranking viable.
- **`confidence` is derived from the distribution shape**, so code can decide
  *when to act* as well as *what to do*.
- **Code owns every threshold and every action.** That is the composite-scoring
  pattern this project uses throughout: one model judgment, weights and thresholds
  in code.

Cost is **input tokens only** (`$42 per billion`, output free), so a judgment is a
fraction of a completion, and one request can carry many questions.

### 8.2 The eight decisions

| # | Decision | Primitives | Deterministic fallback |
|---|---|---|---|
| 1 | **Recall reranking** | one Noul per candidate, all in one request | the hybrid `0.6·vector + 0.4·FTS` score |
| 2 | **Skill suggestion** | Choice over the roster + "needs a skill at all?" Noul | the trigger matcher |
| 3 | **Injection screening** | 2 Nouls + 1 Score per item, batched | plain untrusted tagging |
| 4 | **Skill script gate** | 1 Noul against the skill's own description and source | approval-only, and the trace records that the gate did not run |
| 5 | **Delegation effort** | 1 Noul: "is this several independent things?" | no fan-out: one bounded researcher call |
| 6 | **Answer sufficiency** | 1 Noul + 1 Choice: "is every claim supported?" | no forced revision; the critic still runs and reports |
| 7 | **Capture** | 2 Nouls + 1 Score: durable fact, already in context, importance | one cheap-tier JSON call, behind a deterministic prefilter |
| 8 | **Reflection** | 1 Noul per claim sentence | the cheap-tier fact-checking completion |

Every decision on the reply path that can be answered from supplied text is a
judgment. The remaining model calls were audited one by one to confirm they are
*generation*, not decisions — the ReAct loop, compaction summaries, contextual
chunk headers, dream prose, onboarding prose, embeddings and transcription all
stay with the model, and summarising is not a judgment however tempting it looks.

**#4 is the one binding judgment.** Everywhere else a verdict shapes a heuristic;
here a refusal *stops* an action and no owner approval can override it, because
the threat is a manipulated model asking the owner nicely. It is calibrated rather
than guessed: live calls scored the shipped stdlib-only example script `0.72` and a
credential-exfiltrating variant `0.01`, which is why the gate defaults to `0.60`.

### 8.3 Composition: one term is the model's

The rerank replaces the **relevance** term only:

```python
hit.relevance = (1 - blend) * noul + blend * hit.relevance   # blend default 0.15
hit.score     = hit.relevance * hit.decay * hit.imp_mult
```

Decay and importance stay **deterministic**, because they encode product policy —
what Iris is allowed to forget — and policy must not be outsourced to a model.

Two lanes opt out by design: the pure-cosine ablation must never consult the judge
or it stops measuring anything, and the `no_rerank` ablation exists to A/B it.

### 8.4 Failure policy: fail open, never closed

| Situation | Verdict |
|---|---|
| No key, no SDK, or a request failure | the deterministic path, unchanged from pre-JEV behaviour |
| Screening unavailable | `PASS` with `screened: false` — reported as *unchecked*, never as *checked and clean* |
| A reply-path judgment overruns its budget | the shortlist is used and the turn keeps moving |

Untrusted content stays untrusted either way: screening is an additional gate,
never the trust boundary.

**Latency discipline.** A judgment on the reply path is only free if it is
bounded, so the rerank carries its own budget (`JEV_RERANK_TIMEOUT_SECONDS`,
default 2.5 s) separate from the client timeout. Without it, a slow judgment layer
would cost the full client timeout on every recall — which is how "optional"
quietly stops being true.

### 8.5 Configuration

```dotenv
TYPESAFE_API_KEY=            # empty = JEV disabled; every integration falls back
JUDGE_BACKEND=jev
JEV_ENABLED=true
JEV_MODEL=jev-latest         # pin the version to freeze behaviour
JEV_TIMEOUT_SECONDS=12.0
JEV_RERANK_ENABLED=true
JEV_RERANK_CANDIDATES=20     # shortlist head that gets reranked (one request)
JEV_RERANK_BLEND=0.15        # weight kept for the deterministic score
JEV_RERANK_TIMEOUT_SECONDS=2.5   # reply-path budget
JEV_REFLECTION_ENABLED=true
JEV_REFLECTION_THRESHOLD=0.35    # support probability below which a sentence is flagged
JEV_REFLECTION_MAX_CLAIMS=12
JEV_SKILL_GATE=0.30
JEV_SKILL_MIN_CONFIDENCE=0.30
JEV_GUARD_ENABLED=true
JEV_GUARD_BLOCK_THRESHOLD=0.70
JEV_GUARD_REVIEW_THRESHOLD=0.35
```

### 8.6 Where it is deliberately not used

| Not used | Why |
|---|---|
| Provenance gating, decay math, supersession, SQL, hashing, sandboxes | Deterministic guarantees. A probabilistic model must not sit inside a security or integrity boundary |
| Reply generation, persona, voice | Jev produces no text |
| Choosing which lane to search | Not a heuristic to replace: the agent chooses the lane as a tool argument |
| Compaction, dream prose, chunk headers, onboarding | These need *generation* |
| Embeddings and vector search | No embedding endpoint |
| Images and voice | Text-only input |
| Judging its own output as ground truth | Typed output guarantees the interface, not truth. Calibration is measured across group predictions, not per answer |

### 8.7 Checking it is actually running

```bash
iris chat --once "hello"        # the boot log says jev enabled/disabled and why
curl -s localhost:8000/jev -H "Authorization: Bearer $IRIS_API_TOKEN"
```

`GET /jev` reports `enabled`, the reason it is off when it is, and request/failure
counters with the last latency and last error. A layer that is silently falling
back looks identical to a healthy one without those counters. Every decision also
lands in the turn trace, including the ones that passed and the ones never checked.

| Symptom | Cause | Fix |
|---|---|---|
| `jev disabled (TYPESAFE_API_KEY is not set)` | no key | expected; set the key to enable |
| `jev disabled (typesafe-sdk is not importable)` | SDK missing | `uv sync` |
| 401 / `TypeSafeAuthenticationError` in logs | revoked key, or a mangled copy in `.env` | re-copy it, no quotes, no trailing space. The symptom is quiet, because the caller falls back |
| 429 / `TypeSafeRateLimitError` | rate limit | the SDK backs off and honours `Retry-After`; lower `JEV_RERANK_CANDIDATES` to cut tokens per request |
| Recall ordering looks unchanged | rerank disabled, no key, or an ablation is active | check `/health`'s `jev` flag and the boot line |
| Everything is `PASS` with `screened: false` | screening disabled or unavailable | by design |
| `confidence` below threshold on every skill suggestion | the roster has no matching procedure | correct behaviour — it refuses to guess |

---

## 9. Safety

The threat model is not "the model is malicious". It is three ordinary ones: a
**runaway** (a loop that spends), a **manipulated** model (instructions embedded in
retrieved text), and a **mistake** (a destructive tool call nobody meant). The
safety layer is therefore made of things that are true by construction — policy
data, digests, sandboxes — rather than a prompt asking nicely.

The one rule that makes it checkable: **policy is data, and every decision names
its source.** `iris policy` prints the resolved verdict and where it came from.

### 9.1 The guard chain: refuse before the spend

Every tool call passes a deterministic, pre-dispatch chain:

```
budget → circuit → spiral/dedup → context → record
```

| Guard | Refuses when |
|---|---|
| **budget** | the turn or the day has spent its token ceiling. The day counter persists, so a redeploy does not reset it |
| **circuit** | a tool has failed `TOOL_FAILURE_THRESHOLD` times in a row — its circuit opens for the rest of the run (`unavailable — do not retry`). Three *distinct* failing tools escalate the whole turn. A success resets the streak |
| **spiral** | the same tool runs with near-identical arguments (Jaccard ≥ 0.72) three times, or the turn exceeds its call ceiling. Case and whitespace do not disguise a loop, and legitimately varying arguments are not a false positive |
| **context** | never — it attaches the reason and what to do instead |
| **record** | never — every verdict lands in the turn trace either way |

Nothing in the chain calls a model, because a guard that needs a model to decide
whether to spend money can itself run away. A guard can only ever **refuse**: it
cannot re-enable what another guard closed, and a refusal costs nothing because it
happens before a provider is touched.

`iris guards` prints the same snapshot the engine enforces, with no engine
running. `GET /guards` on a live engine adds the live circuit state, which is
per-run by design.

**Budgets are split by kind** — input, output, cached, embedding, tool-schema —
because they fail differently. `tool_schema` is currently **reserved**: tool schemas
are real spend but arrive inside `prompt_tokens`, and no provider reports them
separately, so nothing increments that bucket. It stays in the schema so a future
source does not change the file's shape under a consumer.

### 9.2 Approvals are bound to what they showed

| Rule | Why |
|---|---|
| The interrupt carries the **effective digest of the arguments after edits** | an edited resume cannot pass as the original |
| One `tool_call_id` grants **once per thread** | a replay cannot re-approve silently |
| Resuming a thread with nothing waiting is refused | it is not handed to the graph to guess |
| A side-effecting action with **no digest fails closed** | whichever way the owner answered |
| A dismissal in an editor dialog is a **refusal**, never an exception and never a silent yes | the ACP path uses the same gate as the CLI's `y/n` |
| A session that **cannot ask** (a scheduled task) is refused *before* an `ask`-policy tool can reach an interrupt nobody can answer | pausing forever is not a refusal |

Approvals are journal events, so a pending approval **survives a restart** — and it
is bound to the digest of the action it showed, not to the process that showed it.

### 9.3 Tool policy

Every tool declares a class, and the class derives its policy.

| Class | Default | Use for |
|---|---|---|
| `read` | `allow` | memory search, traces, stats |
| `filesystem` | `allow` | sandboxed file tools |
| `memory_write` | `allow` | `remember`, `forget`, `note` |
| `network` | `allow` | `web_search`, `ingest_url` |
| `credentialed` | `ask` | anything using an API key |
| `delivery` | `allow` | `send_message`, `send_photo` |
| `control` | `ask` | `computer` |
| `external` | `ask` | a tool that arrived from outside core (MCP, a plugin) — its own source's verdict decides |

**Precedence:** class default < a tool's own source (an MCP server's trust) < a
class override < a per-tool override. **`deny` wins outright at every level**, so
an override can tighten and never loosen — including a class-wide deny that a
per-tool `allow` tries to re-open. A key that names no tool and no class changes
nothing and is *reported*, because a typo in a security knob must be visible.

```bash
iris policy              # classes, overrides and every declared server
iris policy classes      # what each class defaults to, and what moved it
iris policy overrides    # what each override applies to - typos included
iris policy servers      # the rule each MCP server's tools would get
```

**External tools are declared, not free.** A tool that reaches outside the process
is subject to the same policy engine as a core tool, and there is deliberately no
plugin API that bypasses it.

### 9.4 MCP trust belongs to the server

| trust | tool's read-only hint | policy |
|---|---|---|
| any | yes | `allow` — it cannot write, by its own account |
| `owner` | no | `ask` — a human approves before it changes anything |
| `untrusted` (default) | no | `deny` |

`deny` is a **floor**: `approval: "never"` cannot re-open an untrusted write.
Per-server `approval: "always"` means every call, reads included. A denied tool is
missing from the tool list *and* refused at dispatch. Tools arrive namespaced
`server/tool`, so two servers may both ship `search`.

### 9.5 Untrusted content is screened

A `review` or `untrusted` server's reply, and every fetched page, runs through the
injection guard before the model reads it:

```python
hazard = max(injection, exfiltration)
if hazard >= 0.70:                      BLOCK
elif hazard >= 0.35: BLOCK if severity >= 2.0 else REVIEW
else:                                   PASS
```

| Verdict | Effect |
|---|---|
| `PASS` | stored/indexed as ordinary untrusted data |
| `REVIEW` | ingested, but the tool result and the stored import carry a `[UNTRUSTED — SCREENED SUSPICIOUS: …]` banner |
| `BLOCK` | `ingest_url` refuses **before** the write (a hostile page never becomes recallable); `web_search` withholds the body and returns `"trust": "blocked"` |

A blocked block is withheld entirely (`ok: false`, `withheld: true`, no `text`),
and every reply that passes is *tagged* as untrusted data. With no judge available
it is tagged `screened: false` — reported as unchecked, never passed off as checked.

### 9.6 Secrets

| Backend | What it is |
|---|---|
| `auto` (default) | the OS keychain when the optional extra is installed, otherwise a 0600 file |
| `env` | read-only through Iris: a secret in the process environment is inherited by every child process, so this backend **refuses to write** |
| `keyring` | OS-encrypted |
| `file` | 0600, and it says **NOT encrypted** in its own location string rather than letting you assume otherwise |

`${VAR}` in a declared MCP server resolves from the environment first, then the
store — and only in a real load, so a test that supplies its own environment never
sees your stored secrets. `iris secrets` prints names, locations and
`set`/`missing`, and never a value: that is why its output can be pasted into an
issue.

### 9.7 Running a skill's script: four gates

```
agent calls skill_run(name, script)
  → 1. resolves inside that skill's own scripts/?        no → refused
  → 2. deterministic AST pre-screen (network / credential / exec patterns)
  → 3. judgment: does it do only what its skill describes?   below gate → refused
  → 4. owner approval, shown with the findings and the exact arguments
  →    subprocess: constructed environment, timeout, capped output
```

| Property | Detail |
|---|---|
| The judgment gate is **binding** | a refusal cannot be approved away — the threat is a manipulated model asking the owner nicely. With no key the run degrades to approval-only and the trace records that the gate did not run |
| **The environment is built, not cleaned** | only `PATH`, a locale and a temp dir survive, with `HOME` pointed at the skill directory. A script cannot read a key that was never handed to it |
| **Bounded** | a timeout (the manifest's, capped by `SKILL_SCRIPT_TIMEOUT_SECONDS`) and capped output; non-zero exits and crashes come back as data the model reads |
| **One builtin ships as the format's proof** | `skills/web-page-to-notes/` turns a saved page into notes with a stdlib-only script: no network, no environment reads |

**Residual risk, stated plainly:** this is *process* isolation, not *kernel*
isolation. Approving a script runs it as the same OS user as Iris, with that
user's filesystem access — an approved script can still read anything that user
can read.

#### The container level

`EXEC_SANDBOX=container` runs the same script inside a container instead:
`--network none`, `--read-only` with the skill mounted `:ro`, memory and PID caps,
`--user 65534:65534`, `--rm`. The skill's own directory is the only host path that
exists inside.

**It fails closed, on purpose.** If `EXEC_SANDBOX=container` and no container
runtime is on `PATH`, the script does **not** run and the refusal names both ways
out. A level that quietly drops to a weaker one when its dependency is missing is
a level nobody can rely on.

**Residual risk:** a rootless container is a real boundary for what a script can
*reach*, and it is not a hypervisor — a kernel exploit or a misconfigured runtime
is still a kernel exploit. It is also only as good as its image: `EXEC_CONTAINER_IMAGE`
defaults to `python:3.13-slim`, and a moving tag is a different program than the one
you reviewed. Pin it by digest if that matters to you.

### 9.8 Computer use

Screen control is **off by default**, and off means the `computer` tool is not
registered at all — not present-but-refusing.

| Setting | Meaning |
|---|---|
| `COMPUTER_PROVIDER` | `null` (default, no driver) or `playwright`; anything else fails closed |
| `COMPUTER_ALLOWED_HOSTS` | host suffixes `navigate` may reach. **Empty means nothing may be navigated** |
| `COMPUTER_ALLOWED_APPS` | window/page title suffixes `click`/`type` may act in. Empty means nothing |
| `COMPUTER_MAX_ACTIONS` | actions one approval buys |
| `COMPUTER_CONFIRM_DESTRUCTIVE` | confirmation for `click`/`type`; a keystroke into a credential-looking field confirms regardless |

Every attempted action is appended to `workspace/config/actions.jsonl`: what,
where, allowed-or-not, and **never what was typed** (a length and a digest
instead). Read it with `iris tools actions`.

**Residual risk, stated plainly:** the driver runs in the Iris process with the
same session a browser would have. The allowlists bound *where* an action may go
and the grant bounds *how many*; neither is a hypervisor. Keep computer use to
owner-authored, allowlisted flows.

### 9.9 File tools are jailed

Iris may only touch `SANDBOX_DIR` (default `workspace/sandbox/`).
`file_create`, `file_write`, `file_read` and `file_list` validate every path:
traversal (`..`), absolute paths and drive letters are rejected. She can organise
notes and drafts without ever reaching `.env` or a system file.

### 9.10 What is *not* defended

Stated so nobody assumes otherwise:

- **A determined local attacker** — this is a single-user harness, and anything
  that can read your files can read your workspace.
- **Kernel-level escapes** from a skill's container — see the residual risk above.
- **A prompt-injected instruction the model *reads*.** Screening reduces the
  chance it acts on one; the structural defence is that untrusted origin cannot
  reach curated memory.
- **Cost, if you set both ceilings to `0`.** `0` means "no ceiling".
- **The model itself being correct.** This project's answer is provenance and
  sufficiency judgments, not a promise of accuracy.

---

## 10. Extending Iris

Every capability is a **Protocol + a registry**. Core registers its implementations
under a name, a plugin registers its own, and config selects by name — so adding an
integration is configuration, never a core edit.

### 10.1 The Protocols

| Capability | Protocol | Required surface |
|---|---|---|
| models | `iris_ai.capabilities.models.ModelBackend` | `complete`, `complete_with_tools`, `stream_complete_with_tools`, `embed`, `embed_one` |
| memory | `iris_ai.capabilities.memory.MemoryBackend` | `connect`, `close`, `search`, `escalate`, `stats`, `upsert_chunks`, `delete_file_chunks`, `replace_file_chunks`, `forget_entry` |
| judges | `iris_ai.capabilities.judges.Judge` | `enabled`, `unavailable_reason`, `ask`, `status`, `close` |
| tools | `iris_ai.toolregistry.ToolProvider` | `.name`, `.tools(runtime)` |
| channels | `iris_ai.channels.base.Channel` | `.name`, `.connected`, `connect`, `close`, `send_message`, `send_photo`, `get_chat_history` |
| secret stores | `iris_ai.secrets` registered backend | `.name`, `.get`, `.set`, `.delete`, `.location()` |

`REQUIRED` in each capability module is the same list the conformance test asserts,
exported so the test cannot drift from the documentation.

### 10.2 Entry points (the plugin surface)

| Group | The plugin exposes | Selected by | Listed by |
|---|---|---|---|
| `iris_ai.models` | a factory returning a `ModelBackend` | `MODEL_BACKEND=<name>` | `iris plugins` |
| `iris_ai.memory` | a factory returning a `MemoryBackend` | `MEMORY_BACKEND=<name>` | `iris plugins` |
| `iris_ai.judges` | a factory returning a `Judge` | `JUDGE_BACKEND=<name>` | `iris plugins` |
| `iris_ai.tools` | an object with `.name` and `.tools(runtime)` | installed and enabled by default | `iris plugins tools` |
| `iris_ai.channels` | a factory returning a `Channel` | `CHANNELS_ENABLED` / `[channels]` | `iris plugins channels` |
| `iris_ai.hooks` | a callable `attach(bus) -> None` | attached at boot | `iris plugins hooks` |
| `iris_ai.skills` | installed skill packages | discovered | `iris skills list` |
| `iris_ai.secret_stores` | a secret-store backend | `SECRET_STORE=<name>` | `iris secrets backend` |

### 10.3 The rules every plugin inherits

These hold in `registry.py` and the boot path, so a plugin cannot opt out:

- **Nothing is silently dropped.** A duplicate name is a conflict naming both
  sources. Core owns the name it registers; a plugin taking it over must say
  `replace=True` explicitly.
- **An unknown name is an error, not a no-op.** `MODEL_BACKEND=typo` fails with the
  list of registered backends rather than falling back to something else.
- **Discovery is cheap.** Registering an entry point does not call it, so a
  disabled channel is never imported, let alone constructed.
- **A broken plugin is skipped, not fatal.** A provider that raises while being
  built is logged; a hook that raises while attaching is logged and skipped; a hook
  that raises while *running* is logged and skipped too. None of them costs a reply.
- **Plugin tools are additive.** They are not validated against the core's closed
  set of tool names — that set exists so a *skill manifest* cannot name a tool that
  does not exist — but they are logged when added, so "a core tool nobody declared"
  stays distinguishable from "a plugin's tool".
- **External tools still obey policy.** There is no plugin API that bypasses the
  policy engine, deliberately.

### 10.4 A worked plugin: one tool and one hook

```
iris-audit/
├── pyproject.toml
└── iris_audit/
    ├── __init__.py
    └── plugin.py
```

```toml
[project.entry-points."iris_ai.tools"]
audit = "iris_audit.plugin:AuditTools"

[project.entry-points."iris_ai.hooks"]
audit = "iris_audit.plugin:attach"
```

```python
import json
from datetime import datetime, timezone
from pathlib import Path

from iris_ai.agent.tools import Tool
from iris_ai.config import settings

LOG = Path(settings.workspace_dir) / "config" / "audit.jsonl"


def _last_call() -> str:
    if not LOG.exists():
        return "no tool calls recorded yet"
    lines = LOG.read_text(encoding="utf-8").splitlines()
    return lines[-1] if lines else "no tool calls recorded yet"


class AuditTools:
    name = "audit"

    def tools(self, runtime) -> list[Tool]:
        return [
            Tool(
                "audit_last_call",
                "Show the most recent tool call this agent made.",
                {"type": "object", "properties": {}},
                _last_call,
            )
        ]


def attach(bus) -> None:
    def on_post_tool(tool: str = "", ok: bool = True, **_) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"at": datetime.now(timezone.utc).isoformat(), "tool": tool, "ok": ok},
                    ensure_ascii=False,
                )
                + "\n"
            )

    bus.on("post_tool", on_post_tool, name="audit")
```

```bash
pip install -e ./iris-audit
iris plugins tools     # lists `audit`, and where it came from
iris plugins hooks     # attaches it to a throwaway bus and shows the subscribers
iris chat              # `audit_last_call` is now on the surface
```

### 10.5 The lifecycle bus

| Event | When |
|---|---|
| `turn_start` | before assembly |
| `pre_tool` | before dispatch — a hook here may refuse |
| `post_tool` | after dispatch, with the outcome |
| `on_error` | a tool or node raised |
| `turn_end` | after the reply, before banking |

Hooks run in priority order. Iris's own guard chain registers at priority `-100`,
telemetry at `-50`, and a plugin defaults to `0` — an add-on observes, it does not
pre-empt built-in policy. **A raising hook is logged and skipped**, which is what
keeps telemetry from ever costing a reply.

### 10.6 Recipes

**Add a tool to core.** Implement it and register it in `build_tools`; add its name
to `TOOL_NAMES`; declare its **class** in `TOOL_DECLARATIONS`; put it in a namespace
in `NAMESPACES` with a one-line purpose. Then run the coverage tests: they fail if
you forgot the declaration, declared a tool that does not exist, or left a
namespace that stops partitioning the declarations.

**Add a tool from outside core.** The `iris_ai.tools` entry point above. No core
edit, and `iris plugins tools` proves it landed.

**Add a capability from an MCP server (no code at all).**

```bash
cp config/mcp.json.example .mcp.json
iris mcp add wiki --url http://127.0.0.1:8100/mcp --trust owner
iris mcp test wiki
```

```json
{
  "mcpServers": {
    "wiki":  {"url": "http://127.0.0.1:8100/mcp", "trust": "owner"},
    "notes": {"command": "uvx", "args": ["mcp-server-notes"],
              "env": {"NOTES_TOKEN": "${MCP_NOTES_TOKEN}"}}
  }
}
```

The transport is inferred (`url` = HTTP streamable or `sse`; `command` = a child
process), `${VAR}` resolves through the secret store so a token never sits in a
committable file, and a malformed declaration is a **boot error naming the server
and the key** — a server you believe is connected but is not is worse than a
startup failure. Two refusals worth knowing: `ws://` is refused rather than
silently downgraded (the SDK ships no websocket client transport), and **stdio on
Windows** is refused with the reason, because the selector event loop `iris chat`
uses does not implement asyncio subprocesses — run the server yourself and use
`url`.

**Add a skill.** Skills are procedural memory, and both encodings are discovered
automatically: the flat sidecar pair (`workspace/skills/<name>.json` + `.md`) and
the open **Agent Skills** layout (`<name>/SKILL.md`, <https://agentskills.io/specification>).

```markdown
---
name: my-skill
description: One sentence on when to use this.
license: MIT
metadata:
  iris-triggers: "phrase one, phrase two"
allowed-tools: "memory_search, file_read"
---
# My skill

1. Step one.
2. Step two.
```

```bash
iris skills validate     # exits 1 on errors
iris skills show my-skill
```

| Rule | Why |
|---|---|
| `allowed-tools` **narrows** the turn while the skill is active | a skill can never re-open what the session closed |
| an unknown tool in `allowed-tools` is a validation **error** | a manifest that lies about the surface is worse than a missing one |
| script code runs only through `skill_run` | one gate, one audit trail, one place to reason about |

**Add a channel.** Implement the `Channel` protocol, register it (in-tree) or ship
the entry point (out of tree), then enable it by name:

```bash
CHANNELS_ENABLED=telegram,discord
CHANNELS_DISABLED=                 # deny always wins
CHANNEL_DISCORD_URL=https://...    # per-channel address override
```

The engine discovers plugins, resolves the enabled set, and connects each channel
independently — a down channel is skipped, never fatal, and retried in the
background.

**Add a role.** Declare it with its bounds explicit (tier, recall lane, tool
allowlist, round cap, output cap). A role can only **narrow**: the allowlist
intersects with what the session already grants, and no role may delegate further,
because a subagent that can spawn subagents is a recursion with no bottom.

**Add a guard.** Write a function that can only refuse, call it from
`GuardChain.before()` in the right order, give the reason a next step, and record
it. Everything in the chain must be deterministic and model-free. Its tests belong
next to the others, and the important one asserts that a refused call **never
reaches dispatch** — not that a message was printed.

**Or subscribe instead of editing core.** Anything that only needs to observe or
refuse can be a hook, which is why the bus exists.

**Add a setting.** Add the field with a comment explaining *why* it exists, add it
to `.env.example` (a test fails if you forget), and mention it in this manual if an
operator has to make a decision about it. Prefer a default that preserves today's
behaviour, and `0`/empty meaning "no limit" rather than "limit of zero".

**Add an eval metric or a judgment.** A rate gets a Wilson interval, a mean gets a
bootstrapped one, and a comparison gets a *paired* interval so query difficulty
cancels — then register it in the pre-registered decision rule **before** you run
it, because a rule chosen after seeing the numbers is how every ablation "wins".
For a judgment, ask *"is this a decision about supplied text?"*: if yes, it belongs
in the judgment layer with a deterministic fallback and a trace event; if it needs
generation, it stays with the model and should say so in its docstring.

### 10.7 Testing a plugin

Test the *contract*, not your own convenience wrapper:

```python
async def test_it_satisfies_the_protocol():
    from iris_ai.capabilities.memory import REQUIRED, MemoryBackend

    backend = MyBackend(...)
    assert isinstance(backend, MemoryBackend)     # runtime_checkable
    for name in REQUIRED:
        assert callable(getattr(backend, name))
```

Then assert real behaviour at the seam the harness uses: a tool provider's tool
appears in the tool schemas and its result reaches the model; a channel whose
`connect()` returns `False` leaves boot working; a raising hook does not propagate;
a backend is built *through the registry*, not by importing your class, because the
registry is what the engine uses. Run `pytest` and `ruff` on the plugin repository
too — it is a Python package, and those are the price of admission.

---

## 11. Interfaces

The kernel is one thing reached through several faces. No interface gets behaviour
the library does not expose.

| Interface | Entry point | For |
|---|---|---|
| library | `import iris_ai` | embedding Iris in your own program |
| CLI | `iris` | the fastest way in, and every inspection verb |
| HTTP API | `iris_ai.api:app` (uvicorn) | clients, the Telegram bridge, a dashboard |
| editor | `iris-acp` | Zed, JetBrains, and any other ACP client |
| chat channel | `mcp_servers/telegram/` | talking to her from your phone |

The CLI is documented command by command in [§3](#3-the-cli).

### 11.1 The HTTP API

| Route | What it does |
|---|---|
| `GET /health` | liveness + judgment summary + in-flight background work (**unauthenticated on purpose** — it is the readiness probe) |
| `GET /jev` | judgment-layer health: enabled or not, *why not*, counters, last latency and error |
| `POST /chat` · `POST /chat/stream` · `POST /chat/resume` | one turn as JSON, as SSE, or a resumed approval |
| `POST /voice` | a Telegram voice note → transcript → turn |
| `GET /onboarding` | whether a profile has been saved |
| `POST /sleep` | run the dream cycle now |
| `GET /mind` · `GET /skills` · `GET /tasks` | memory snapshot, learned skills, scheduled jobs |
| `GET /rot` · `GET /retention` | forgetting report |
| `POST /forget` · `POST /forget/confirm` | the two-phase forget |
| `GET /costs` · `GET /traces` | ledger rollups and per-turn traces |
| `GET /guards` | the live guard chain snapshot, including open circuits |
| `POST /cron/reload` | pick up jobs added while the engine was running |

**Auth.** `IRIS_API_TOKEN` protects every route except `/health` with an
`Authorization: Bearer` check, from one shared definition
(`iris_ai.security.auth_headers`), so a client and the server cannot disagree about
the scheme. When it is unset, auth is off and the core logs a warning at boot.
Set it for anything beyond localhost.

The SSE contract is shared with every client through one `BrainClient` definition —
a client must not be able to disagree with the server about the shape.

### 11.2 ACP — Iris in an editor

The [Agent Client Protocol](https://agentclientprotocol.com) is how an editor
talks to an agent it spawned. `iris-acp` speaks ACP on stdin/stdout and forwards
every prompt into the *same* harness the CLI and API use.

```bash
pip install "iris-personal-ai[acp]"
iris-acp          # the editor spawns this; you normally do not run it by hand
```

```json
{
  "agent_servers": {
    "Iris": { "command": "iris-acp", "args": [] }
  }
}
```

| ACP | Iris |
|---|---|
| `initialize` | protocol version 1, `loadSession: true`, text-only prompts, `agentInfo` from `iris version` |
| `session/new` | a new session id ⇒ a new `acp:<id>` thread |
| `session/load` | an existing id ⇒ the same thread, so memory continues |
| `session/list` | sessions this process knows, optionally filtered by `cwd` |
| `session/close` | forget the handle (and cancel a running turn) |
| `session/cancel` | cancels the running turn; the prompt answers `stopReason: cancelled` |
| `session/prompt` | one kernel turn, streamed back |
| `session/update` | streamed text deltas, plus the final reply when nothing streamed |
| `session/update` (tool) | `start_tool_call` on the request, `update_tool_call` with `completed` or `failed` |
| `session/request_permission` | a kernel approval interrupt |

`session/set_mode` is deliberately absent: Iris has no modes, so the router answers
`method not found` rather than inventing a no-op.

**The four decisions the adapter makes** — this is the short list, and each one is
a place where the protocol and the harness disagree about something:

1. **One harness, many sessions.** ACP session ids map to threads as `acp:<id>`, so
   `session/load` continues the same conversation after an editor restart, and an
   ACP thread cannot collide with the CLI's `cli` thread. `session/close` forgets
   the *handle*, not the memory.
2. **The editor's `cwd` does not widen the filesystem.** The session records it,
   reports it back and logs it — but file tools keep running against the harness
   workspace and sandbox. A client asking for a directory is not an owner granting
   it. Client-supplied `mcpServers` are treated the same way: logged and **not
   adopted**.
3. **An approval is a permission request.** It travels through the same gate the
   CLI's `y/n` uses, so a grant is single-use and digest-bound, a dismissed dialog
   is a refusal, and the dialog shows the action, the digest and what approving
   allows — **never the arguments**, for the same reason traces hash them.
   `MAX_APPROVALS_PER_PROMPT` bounds a prompt that keeps asking.
4. **Unsupported prompt content is an error, not a silent drop.** Text blocks are
   flattened, a `resource_link` becomes a visible reference line, and anything else
   (`image`, `audio`) is refused with `invalid_params` — dropping a block would
   answer a question you did not ask.

The **resumed** half of an interrupted turn returns the kernel's final reply; the
tool calls it makes while resuming are journalled but not streamed. Saying so here
is cheaper than a bug report asking why a resumed turn has no tool cards.

**stdout belongs to the protocol.** The streams are taken before anything else and
stdout is then redirected to stderr for the rest of the process, so a stray `print`
is a log line instead of a JSON-RPC parse error.

**Windows caveat.** The protocol's stdio transport needs `connect_write_pipe`,
which the selector event loop does not implement — so `iris-acp` leaves the event
loop on the platform default. psycopg wants the selector loop, and the two cannot
both win, so **on Windows an ACP session stores threads in SQLite even when
Postgres is configured**. Threads still survive a restart; they live in a file.

Where the tests are: `tests/test_acp_adapter.py` covers the mapping decisions and,
for one case, the real wire — an actual `ClientSideConnection` over an in-memory
transport, so the method names and models are the SDK's rather than a
re-implementation of them. Streamed approvals are also the CLI's path, which is why
the ACP and chat-CLI tests fail together when the interrupt is read wrongly.

### 11.3 The Telegram bridge

The bridge (`mcp_servers/telegram/`) is a custom **MCP 2.0 server** (streamable
HTTP) that wraps the Telegram Bot API with long-polling. It exposes `send_message`,
`send_photo`, `get_chat_history` and `broadcast` to Iris and forwards your inbound
messages to the agent API.

| Command | What it does |
|---|---|
| `/start` | bind this chat as the owner (ignored once bound) |
| `/sleep` | run the dream cycle now |
| `/wake` | a morning-style briefing (chunks, decay, rot) |
| `/mind` | inspect what she currently knows |
| `/forget <text>` | supersede a memory, two-phase with an explicit confirm |
| `/forget-confirm` · `/forget-cancel` | complete or abort a pending forget |
| `/skills` · `/tasks` | learned skills, pending jobs |
| `/rot` · `/retention` | forgetting report |
| `/help` | the command list |

There is deliberately no `/remember` or `/dream_now`: durable facts are written by
the agent's own tools during conversation, and `/sleep` covers consolidation.

Since the rewrite, the bridge is **a client of the library, not a second brain**:

- Turns and commands go through `HttpBrainClient` — one definition of the HTTP/SSE
  contract, shared with the CLI. The bridge keeps only transport concerns
  (long-polling, sending, progressive edits, typing, downloads, the owner gate).
- Updates are normalized and **de-duplicated** by an `UpdateLedger` persisted beside
  `owner.json`, so a restart resumes from the stored offset instead of replaying
  already-handled updates into fresh turns.
- Once an owner is bound the bot is **private**: any other chat is refused. Set
  `OWNER_CHAT_ID` to pin ownership in the environment instead of letting the first
  `/start` claim it.
- HITL approvals are surfaced inline ("I'd like your OK before touching that
  memory") and the thread is kept unblocked automatically. While a turn is in
  flight, Telegram shows **typing…** and the API streams thinking plus tool calls.

### 11.4 Clients vs channels

| | What it is | Examples |
|---|---|---|
| **Clients** | things that talk *to* Iris | CLI, HTTP clients, ACP adapters, the Telegram bridge. Idempotency is the client's job |
| **Channels** | transports Iris reaches *out* through | `Channel` protocol implementations, registered and enabled by config, outbound and gated by policy |

---

## 12. Observability and cost

Three layers, deliberately different things rather than three copies of one:

| Layer | What it is | Where it lives | Cost when unused |
|---|---|---|---|
| **Turn traces** | the local, always-on record: which prompt version ran, which tools were called, how each stage spent its time, which judgment was made | `workspace/config/traces.jsonl` | ~nothing; an append off the reply path |
| **Spans** | the *same* events shaped for the GenAI semantic conventions, so your existing backend shows Iris turns and tool calls as first-class spans | OTLP, only when asked | zero: the SDK is an optional extra |
| **Cost ledger** | every model call: model, tokens, cached tokens, price | `workspace/config/llm_calls.jsonl` | one append per call |

The rule that keeps this a feature instead of a tax: **tracing is never on the
critical path.** Traces and the ledger are written after the reply is produced, and
telemetry is a *subscriber* to the hook bus rather than a branch in the agent loop.

### 12.1 Traces

One JSON line per turn, rotated and bounded. Each line carries the timestamp,
session, latency, the tools called, whether an approval is pending, what the capture
node wrote, an `events` list, `stages_ms`, and per-call throughput.

| Field | Why it exists |
|---|---|
| `events` | every recall rerank with the probability it gave each candidate, every guard verdict **including the ones that passed**, the skill decision with its gate inputs, the capture verdict and its rejection reason, and whether reflection ran inline or in the background. A judgment nobody can inspect is indistinguishable from one that silently failed |
| `stages_ms` | `assemble`, `agent`, `tools`, `rerank`, `guard`, `capture`, `reflection`, `jev` — so "it felt slow" becomes a stage |
| `throughput` | separates **TTFT from TPOT** on the streamed path; a single total latency cannot say whether a p99 went to prefill or to generation |

**Trace content policy** (`IRIS_TRACE_CONTENT`): `metadata` (default), `redacted`,
`sampled` or `full`. **Credentials never reach the trace file in any mode**, and
tool arguments are recorded as a digest rather than verbatim — scheduled runs happen
with nobody watching, which is exactly why this is explicit rather than assumed.

### 12.2 Spans

```bash
pip install "iris-personal-ai[otel]"
```

```dotenv
OTEL_EXPORTER=otlp
OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces
AGENT_NAME=iris                                  # becomes gen_ai.agent.name
```

`OTEL_EXPORTER=none` (the default) keeps a single-user harness free of a telemetry
stack. `otlp` without the extra is a **boot error naming the extra** — never a quiet
no-op, because an operator staring at an empty dashboard with no error to read is
the worst version of this feature.

| Span | Attributes |
|---|---|
| `turn` (one per `turn_end`) | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, `iris.turn.duration_ms`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model` when known, `error.type` on failure |
| `<tool name>` (one per `pre_tool`/`post_tool` pair) | `gen_ai.operation.name=execute_tool`, `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `iris.tool.side_effecting`, `iris.tool.digest`, `error.type` on failure |

**Tool arguments are a digest, never an attribute.** A credential in a tool argument
must not become a span attribute in someone's SaaS backend — the same rule the
traces follow, for the same reason.

Two guards keep the shapes honest rather than merely intended:

- `iris_ai.observability.spans.GENAI_ATTRIBUTES` is the complete set of keys this
  project will ever emit, and a test asserts every span's attributes are a subset.
- The sink drops a stray key with a warning, because OpenTelemetry does not error on
  an undefined attribute name — it arrives at the backend as *nothing*, so a typo
  would otherwise silently vanish.

The shaping module is pure: no SDK, no collector, no network. That is why the
convention guard and the refusal path are unit-tested in an environment with no
telemetry installed.

### 12.3 The cost ledger

```bash
iris costs            # totals, and a by-model table
iris costs daily      # the last 14 days (-n 30 for another window)
iris costs weekly     # the last 4 weeks
```

Two honesty rules, carried from the ledger itself: a model with **no price** in
the table is *named*, so a total is never quietly lower than reality, and an empty
ledger says so rather than printing a confident `$0.00`. Unknown models price at
`0.0` and appear in `unpriced_models`.

### 12.4 Evaluation

An eval that reports a point estimate on a small set is a coin flip with a chart.
`iris_ai.eval.stats` (stdlib only, so the arithmetic is unit-tested without a
database or a model) supplies:

- **Wilson intervals** for rates, a **seeded bootstrap** for means, and a
  **paired** interval for "candidate vs baseline" so query difficulty cancels;
- a measured **noise floor**, and sample sizing that derives the figure rather than
  guessing it;
- **Cohen's κ** for judge–human agreement;
- a **pre-registered decision rule** that reports `inconclusive` rather than a pass
  when it cannot pass.

`scripts/eval_lab.py` renders those intervals instead of points (writing
`reports/eval_lab.md`, which is generated and not committed).

**The retrieval gate is a CI job, not a report.** `tests/test_retrieval_gate.py`
scores a labelled fixture with `recall@k` and `nDCG@k` against a real pgvector index
with **no model in the loop**, so a ranking regression names itself in the checks
list instead of hiding inside a thousand-test run — and cannot be talked away by a
judge model.

### 12.5 Reflection

Turns that actually retrieved memory get a pass that flags claims not supported by
the retrieved excerpts (`config/hallucination_flags.jsonl`, counted in `/mind`). It
runs **off the reply path** by default (`IRIS_REFLECTION_BACKGROUND=0` to run it
inline): it only appends to a telemetry file, so making the owner wait on it was
pure latency. It is a tracked background task rather than a bare
`asyncio.create_task`, because the loop holds only a weak reference and can
garbage-collect a task mid-flight; `background.drain()` is awaited on shutdown.

---

## 13. Models and providers

Two tiers (strong/cheap) reached through LiteLLM, behind a `ModelBackend` Protocol
so a native SDK backend can be registered without touching the kernel. Every
provider lives in **one table** (`src/iris_ai/providers.py`) holding its name, key
variable, model-id prefix and base URL — `Settings`, the failover chain and
`iris doctor` all read that table, so adding a provider cannot leave one of them
behind.

| Provider | Env var (key) | Strong (default) | Cheap (default) | Notes |
|---|---|---|---|---|
| **OpenRouter** | `OPENROUTER_API_KEY` | `openrouter/nvidia/nemotron-3-super-120b-a12b:free` | `openrouter/nvidia/nemotron-nano-9b-v2:free` | free `:free` variants; override with `OPENROUTER_STRONG_MODEL` / `_CHEAP_MODEL` |
| **Groq** | `GROQ_API_KEY` | `groq/openai/gpt-oss-120b` | `groq/openai/gpt-oss-20b` | free tier, very fast. Ids verified 2026-09-21 |
| **Gemini** | `GEMINI_API_KEY` | `gemini/gemini-3.5-flash` | `gemini/gemini-3.1-flash-lite` | also powers embeddings |
| **OpenCode Zen** | `OPENCODE_API_KEY` | `openai/deepseek-v4-pro` | `openai/deepseek-v4-flash-free` | curated gateway with free models; base URL `https://opencode.ai/zen/v1` |
| **OpenCode Go** | `OPENCODE_API_KEY` *(shared)* | `openai/deepseek-v4-pro` | `openai/deepseek-v4-flash` | same key, endpoint `https://opencode.ai/zen/go/v1` |
| **OpenAI** | `OPENAI_API_KEY` | *(you set it)* | *(you set it)* | set `OPENAI_STRONG_MODEL` / `_CHEAP_MODEL` |
| **DeepSeek** | `DEEPSEEK_API_KEY` | *(you set it)* | *(you set it)* | e.g. `deepseek/deepseek-chat` |
| **xAI Grok** | `XAI_API_KEY` | *(you set it)* | *(you set it)* | e.g. `xai/grok-4` |
| **Mistral** | `MISTRAL_API_KEY` | *(you set it)* | *(you set it)* | e.g. `mistral/mistral-large-latest` |
| **Together** | `TOGETHER_API_KEY` | *(you set it)* | *(you set it)* | hosted open weights |
| **Fireworks** | `FIREWORKS_API_KEY` | *(you set it)* | *(you set it)* | hosted open weights |
| **Any OpenAI-compatible** | `OPENAI_COMPATIBLE_API_KEY` | *(you set it)* | *(you set it)* | vLLM, LM Studio, a self-hosted gateway: also set `OPENAI_COMPATIBLE_BASE_URL` |
| **Ollama** | *none* (local) | `ollama/qwen2.5-coder:3b` | `ollama/qwen2.5-coder:3b` | `ollama serve` + `ollama pull <model>` |
| voice (always Groq) | `GROQ_API_KEY` | — | — | `groq/whisper-large-v3-turbo` |
| web search (optional) | `TAVILY_API_KEY` | — | — | free tier at tavily.com |
| **JEV (optional)** | `TYPESAFE_API_KEY` | — | — | judgments; no key = deterministic fallbacks |

### 13.1 Empty model ids are deliberate

Providers with no pinned id ship **empty on purpose**. Model names drift
constantly — two Groq defaults in this repo were wrong at runtime, which is why the
ids that do ship carry the date they were checked. A guessed id is a 404 on every
turn rather than an error you can see, so a provider with a key but no model id is
*skipped*, never attempted, and `iris doctor` names it:

```text
$ iris doctor
  + ok   provider keys: OPENCODE_API_KEY
  + ok   model provider: OpenCode Zen (LLM_PROVIDER=opencode)
  ! warn strong model: OPENAI_STRONG_MODEL is unset - OpenAI cannot be used yet
```

### 13.2 The message contract

The Protocol owns it: provider roles are `system|user|assistant|tool`, and a
LangChain role (`human`/`ai`) must never cross the boundary. A stray `human` once
reached four providers as one failure, so the rule is enforced in the adapter *and*
asserted by a conformance test — not left to a comment.

### 13.3 Embeddings are the one hard constraint

OpenRouter and Groq do not offer embeddings. Iris uses Gemini when
`GEMINI_API_KEY` is set, otherwise it falls back to `ollama/nomic-embed-text`
automatically — so an OpenRouter-only or Groq-only setup still indexes, as long as
Ollama is running. With neither, recall is keyword-only and says so.

### 13.4 Reliability

| Behaviour | Detail |
|---|---|
| Retries | exponential backoff with full jitter; never retry a 4xx except 429 |
| `Retry-After` | parsed and honoured |
| Timeouts | four clocks: connect, first token, stream idle, total |
| Mid-stream failure | what was buffered is flushed rather than losing a partial reply |
| Tier pinning | a role may be pinned to the *other* tier to break self-preference bias (the critic does exactly this) |
| Sampling quirks | per model, not global: Gemini 3+ omits `temperature`; reasoning models get no temperature and a different max-tokens meaning |
| Cost | budgets are enforced in the kernel; the gateway records usage and does not decide limits |

---

## 14. Scheduling and proactivity

### 14.1 Jobs

Jobs live in `workspace/config/tasks.json` and are created either by asking her in
chat (*"remind me in 3 days to renew the lease"*, *"every Monday at 9 run the
weekly review"*) or from the CLI. **One grammar parses recurrence in both places** —
the CLI reuses the agent's parsers rather than inventing a second one.

| Kind | Example | Fires |
|---|---|---|
| `once` | `in 3 days`, `2026-10-01T09:00` | once, then removed |
| `every` | `every 6h`, `every 2d` | on an interval, anchored to the last run |
| `calendar` | `daily at 09:00`, `mon,wed,fri at 18:30` | on the wall clock, in your timezone |

### 14.2 Missed windows are a declared policy

| Situation | Outcome |
|---|---|
| Iris was down across several windows of a recurring job | fires **once** (coalesced) — never once per missed window |
| A one-off job's time passed while offline | counted as `missed` and recorded *before* it is dropped, so "why didn't my reminder fire?" has an answer |
| A job keeps raising | its failure count rises and past the threshold the job **disables itself** rather than retrying forever |
| Nothing is due | the tick does nothing and never crashes the process |

Every job carries its own history — `runs`, `misses`, `failures`, `last_outcome`,
`last_run`, `last_error` — and `iris cron list` shows exactly that.

```bash
iris cron list
iris cron add --every "6 hours" --instruction "summarize new notes in the vault"
iris cron add --at "mon,wed,fri at 18:30" --instruction "what shipped today?"
iris cron rm 3f9a
```

**A scheduled run cannot write durable memory.** The instruction runs through the
same graph with `origin="task"`, and the provenance rules refuse a non-owner,
non-agent source for curated memory — so a time trigger cannot poison the vault. A
session that runs unattended is also refused *before* an `ask`-policy tool can reach
an approval nobody can give.

`iris cron add` writes to the store; a **running** engine picks it up on
`POST /cron/reload` (the CLI prints that reminder rather than pretending the job is
already live).

### 14.3 Circadian proactivity

Two jobs run in your timezone:

- **Nightly sleep** (`NIGHTLY_SLEEP_HOUR`, 04:00) — the dream cycle consolidates the
  day into `MEMORY.md`.
- **Morning brief** (`MORNING_BRIEF_HOUR`, 08:00) — a channel digest: what dreaming
  promoted, how retention looks, what is flagged as rot. Needs a bound owner and a
  connected channel; otherwise it stays silent.

Both are configured in `config.py`, not stored as jobs — which is why `iris cron rm`
refuses to remove them and says where they live. Both are no-ops when they cannot
run safely; the scheduler never crashes the process.

---

## 15. Deployment

Iris is not a serverless workload. She needs:

- an **always-on process** — Telegram long-polling plus the scheduler hold state
  that a scale-to-zero host would tear down between turns, and a 120 s turn budget
  does not survive a cold start;
- a **persistent volume for `workspace/`** — this is not a cache, it *is* the
  product;
- optionally **Postgres with the `vector` extension** — only if you choose the
  `pgvector` backend. A vanilla Postgres image cannot run it.

That rules out pure function platforms. The realistic options:

| Host | Fits because | Trade-off |
|---|---|---|
| **Fly.io** | machines stay on, managed Postgres offers pgvector, volumes attach directly, cheap for one small machine | Postgres is a separate app to manage |
| **Railway / Render** | Dockerfile deploys straight from the repo, managed pgvector, persistent disks, HTTPS and a domain | a persistent disk is a paid add-on; sleeping plans break the scheduler |
| **A small VPS** + `docker compose` | full control, the compose file in this repo *is* the deployment, cheapest per GB | you own patching, TLS and backups |

### 15.1 The three rules that bite

1. **Run the container as uid 10001** (it already does) and give it a workspace it
   can write: `sudo chown -R 10001:10001 ./workspace`. A bind mount keeps the
   host's ownership, so skipping this is the single most common "Iris cannot
   remember anything" failure — writes fail into the log while the API stays up.
2. **`/health` is the readiness probe** (unauthenticated on purpose). Wire it to the
   platform's health check, or use the image's `HEALTHCHECK`.
3. **Never bake `.env` into an image.** `.dockerignore` excludes it; set env vars in
   the platform's secret manager instead.

### 15.2 Environment for a deployment

| Variable | Why |
|---|---|
| `LLM_PROVIDER` + one provider key | the model |
| `GEMINI_API_KEY` **or** a reachable Ollama | embeddings. Without either, recall is keyword-only |
| `POSTGRES_DSN` | only if `MEMORY_BACKEND=pgvector` |
| `IRIS_API_TOKEN` | the API is the only thing between the internet and your memory; boot warns when unset |
| `OWNER_CHAT_ID` | pins bridge ownership **and** enables the morning brief |
| `IRIS_TIMEZONE` | drives the sleep hour, the brief and every daily-note timestamp |
| `TYPESAFE_API_KEY` | optional judgment layer |

### 15.3 Backups

Two things, in this order:

1. **`workspace/`** — the memory itself. It is plain Markdown and JSONL, so a
   nightly `tar czf iris-workspace-$(date +%F).tar.gz workspace/` plus a copy
   off-host is a complete backup. Restoring is unpacking it; the index rebuilds on
   the next boot.
2. **The database**, if you use Postgres — `pg_dump` on the same schedule. It is
   *derived*, so losing it costs a reindex rather than data, but the thread
   tables hold in-flight conversations and pending approvals.

**A restore that only restores the database is not a restore.**

### 15.4 Rollback

The app is a single image, so a rollback is a re-deploy of the previous tag:

```bash
docker compose pull iris-core && docker compose up -d iris-core
```

Schema changes are additive (`postgres/init.sql` runs only on an empty volume), so
an older image can run against a newer database — and the workspace is untouched by
deploys either way.

### 15.5 Demo mode

To give visitors something to try without exposing real memory, deploy a second
instance with its own empty workspace, its own database, `IRIS_API_TOKEN` set, and
**no** Telegram token so the demo has no channel into anything real.

---

## 16. Testing and CI

### 16.1 Running the suite

```bash
uv sync --all-extras                                  # tests for the optional extras need them
uv run ruff check .                                   # lint is a gate, not a suggestion
uv run pytest tests -q --ignore=tests/test_memory_pipeline.py \
                       --ignore=tests/test_retrieval_gate.py
uv run pytest tests -q                                # everything; needs the pgvector database
uv run python scripts/eval_lab.py                     # ablation study -> reports/eval_lab.md
```

Two files need a real pgvector service and **fail loudly** rather than skipping,
because a silently-skipped integration test is a test nobody has:
`tests/test_memory_pipeline.py` and `tests/test_retrieval_gate.py`. They use their
**own** databases (created on demand by the fixtures that need them), so a test run
can never touch real memory; point them elsewhere with `IRIS_TEST_POSTGRES_DSN`.

### 16.2 What CI proves, and where

| Check | Job | Why there |
|---|---|---|
| `ruff check .` | `lint` | lint is a gate |
| wheel build + install + `iris version` / `iris --help` | `package` | the wheel is the artifact. A build that only exists in `pyproject.toml` is a claim, not a deliverable |
| the full suite, including the DB-backed files, with `--all-extras` | `test` | the optional extras' tests must run too, or 12 ACP tests silently skip |
| `recall@k` / `nDCG@k` over a labelled fixture, no model in the loop | `retrieval` | a hand-generated report is not a gate; a ranking regression fails the PR |
| the production image builds and runs as non-root | `build` | a Dockerfile is only correct if it builds. The wheel's metadata needs `LICENSE` in the context and its forced package data needs `skills/` and `assets/` |
| the five-minute path, **timed**, with **no Postgres service in the job** | `onboarding` | clone → `uv sync` → `iris init --offline` → a reply, failing past 300 s. "No daemon" is half the claim, so the job that tests it has no daemon |
| dependency audit + static analysis | `security` | a pinned lockfile is not the same as a safe one: this job caught a transitive HTTP-client version with three advisories. Secret scanning runs as GitGuardian's app on the repository |

### 16.3 The compatibility matrix

Each optional surface is additive: absent, its feature degrades *with a reason*
rather than failing the boot, and no core path imports it.

| Surface | Extra / requirement | When it is absent |
|---|---|---|
| Editor integration (ACP) | `pip install "iris-personal-ai[acp]"` | `iris-acp` exits non-zero naming the extra; `iris` is untouched |
| Telemetry (OTLP) | `pip install "iris-personal-ai[otel]"` | `OTEL_EXPORTER=none` is the default; asking for `otlp` without the extra is a boot error naming it |
| MCP servers, HTTP/streamable | the `mcp` SDK (a core dependency) | no declared servers means no external tools, and nothing is started |
| MCP servers, stdio | the SDK **and** an event loop with subprocess support | refused with the reason: on Windows the CLI's selector loop does not implement asyncio subprocesses — run the server yourself and use `url` |
| MCP servers, `ws://` | *not supported* | refused rather than downgraded, because the SDK ships no websocket client transport |
| Secret store, OS keychain | `pip install keyring` | `SECRET_STORE=auto` chooses a 0600 file and says so |
| Skill sandbox, container level | `EXEC_SANDBOX=container` + Docker | **fails closed**: the call is refused rather than run at the weaker process level |
| Computer use | `COMPUTER_ENABLED=true` + Playwright | the `computer` tool is not registered at all, and availability reports a stable reason |
| `sqlite-vec` native vectors | *not used* | the SQLite backend always runs FTS5 keyword recall and, with an embedder, brute-force exact cosine. Chosen over a native extension so the default path has no build step |
| Postgres + pgvector | a server with the `vector` extension | SQLite is the default; selecting pgvector without a reachable server is a degraded boot that names the reason |

### 16.4 Supported environment

| Axis | Supported | Notes |
|---|---|---|
| Python | 3.12, 3.13 | `requires-python = ">=3.12"` |
| OS | Linux (CI, containers), Windows (development), macOS (expected) | the skill runner uses a worker thread around `subprocess.run` on purpose: the API selects the Windows selector loop for psycopg, where asyncio subprocess support is unimplemented. The suite passes on Windows; CI is Linux |
| Memory store | **SQLite by default — nothing to install** | Postgres + pgvector is the scale-up option. With the configured store unreachable, Iris boots degraded (or refuses, for the API's `require` mode) and `mode`/`degraded_reason` say which |
| Disk | `workspace/` holds all memory as Markdown + JSONL | back it up with `tar` |
| Docker | optional | needed for Postgres, the full API + bridge stack, and the container sandbox level. The default path starts nothing |

### 16.5 Version discipline

- **One source of truth.** The version lives in `src/iris_ai/__init__.py` and
  hatchling reads it from there, so the package metadata and `iris version` cannot
  drift. A test asserts this.
- **The changelog is the release note.** `CHANGELOG.md` is written per phase,
  newest first, and every number in it names the method that produced it.
- **Cutting a release** (an explicit, deliberate act — Iris never tags or pushes
  itself): decide the version and edit `src/iris_ai/__init__.py`; move the
  `Unreleased` section of `CHANGELOG.md` under the new version; `uv build` and
  confirm the `package` job is green; then tag and push.

---

## 17. Troubleshooting

### Boot and setup

| Symptom | Cause | Fix |
|---|---|---|
| `recall: keyword-only` | no embedding provider | set `GEMINI_API_KEY`, or run `LLM_PROVIDER=ollama` with `ollama/nomic-embed-text` |
| `model check: fail` | no usable provider key, or `LLM_PROVIDER` names one you have no key for | `iris doctor` prints the key **names** it found |
| `threads: in-memory` | neither Postgres nor the SQLite path was usable | check `CHECKPOINTER_PATH` is writable |
| `model not found` on the first turn | the model id does not exist for that provider | `iris doctor` lists the resolved provider; pin a real id or leave it empty and pick another provider |
| `no LLM provider key found` from `iris chat` | nothing in `.env` or the environment | `cp .env.example .env`, then set one key |
| A command exits non-zero after `init` | the report's last line is the reason | `1` means a `fail`, not a `warn` |
| `degraded` and recall is unavailable | the memory backend could not connect | the message names the store; `iris doctor` shows the configured one |

### Memory and recall

| Symptom | Cause | Fix |
|---|---|---|
| Nothing is remembered | the workspace is not writable (container ownership is the usual culprit) | `chown` the volume to uid 10001; check `WORKSPACE_DIR` |
| Recall misses things you know are stored | keyword-only mode, or the entry is in a daily note old enough to have decayed | enable embeddings; use the escalation lane ("when did I…"), or check `iris chat`'s `/retention` |
| `MEMORY.md` never grows | consolidation only happens in the dream cycle, and capture only writes add-only daily notes | run `/sleep`, or the nightly job; check `config/hallucination_flags.jsonl` and the trace's `capture` field |
| The index looks wrong after switching stores | you skipped the migration | `iris migrate --to sqlite` rebuilds it from the Markdown |
| `iris migrate --to null` refuses | `null` stores nothing | pick `sqlite` or `pgvector` |

### Safety and policy

| Symptom | Cause | Fix |
|---|---|---|
| A tool call is refused with a reason | the guard chain: budget, circuit, spiral, or policy | the message names the guard; `iris guards` shows the ceilings, `iris policy` the rule |
| A tool is missing entirely | `deny` at some level, or the tool is not registered (computer use off) | `iris policy` shows the resolved verdict *and* its source |
| An MCP tool never appears | the server's trust denies a write | `iris mcp test <name>`; `--trust owner` for a server you wrote |
| A schedule is refused before running | a session that cannot ask may not reach an `ask`-policy tool | widen the policy for that tool, or remove it from the instruction |
| A skill's script will not run | the judgment gate refuses it, or the sandbox level failed closed | the refusal names the gate and says whether it ran; `EXEC_SANDBOX=process` is the weaker, working level |
| An approval does not resume | the digest changed, or the approval was already spent | approvals are bound to what they showed and granted once per thread; ask again |

### Provider and cost

| Symptom | Cause | Fix |
|---|---|---|
| A provider with a key is skipped | it has no pinned model id | set `<PROVIDER>_STRONG_MODEL` / `_CHEAP_MODEL` |
| 401 from a provider | a mangled key in `.env` | re-copy it: no quotes, no trailing space |
| 429s and slow turns | free-tier rate limits | the client throttles and backs off; move the cheap tier, or cut `JEV_RERANK_CANDIDATES` |
| `iris costs` shows a total lower than your bill | a model with no price in the table | the output names it in `unpriced_models`; add it to the price table |
| An empty `$0.00` | the ledger is empty | it says so rather than pretending; `iris chat` is the quickest way to fill it |

### CLI

| Symptom | Cause | Fix |
|---|---|---|
| `error ...` with no traceback | the default behaviour | re-run with `--debug` (or `IRIS_DEBUG=1`) for the original traceback |
| No banner / no colour | piped, `NO_COLOR`, a non-terminal, or `IRIS_NO_BANNER=1` | that is deliberate: logs and scripts get plain text |
| Garbled marks in a Windows console | the console's codepage, not the command | Iris's own characters are cp1252-safe; a modern terminal or `chcp 65001` renders them |

### Editor (ACP)

| Symptom | Cause | Fix |
|---|---|---|
| The agent fails to start in Zed | the `acp` extra is missing, or the boot failed | run `iris-acp` by hand: a boot failure is printed on stderr and exits non-zero |
| An `image` or `audio` prompt is rejected | prompts are text-only | attach a `resource_link`, or paste the text |
| On Windows, threads are files even with Postgres | the stdio transport needs the platform-default event loop, and psycopg wants the selector loop | expected and documented; threads still survive a restart |
| A closed tab lost the conversation | it did not: `session/close` forgets the handle, not the memory | `session/load` with the same id continues it |

---

## 18. Working on Iris

### 18.1 The house rules

- **Every extension lands with a test that would fail if the behaviour were
  removed**, and nothing is relaxed to get green. If a change makes an existing test
  awkward, that test is either wrong (fix it and say so) or the change is.
- **Comments explain why.** "Sort by score" is noise; "sorting by score keeps the
  cheap tier from outranking a source the owner pinned" is the reason the next person
  needs.
- **Docstrings state the contract and the degradation.** Every optional layer says
  what happens when it is absent.
- **Errors are values where the caller can act**, exceptions where it cannot.
- **Never print a secret value.** `iris doctor` prints names; traces hash arguments.
- **Tests are named for the invariant**, not the function: `test_deny_beats_a_class_allow`,
  not `test_resolve_2`.
- `from __future__ import annotations`, `slots=True` dataclasses, `StrEnum` for closed
  vocabularies, and no new dependency without a reason in the commit message.

### 18.2 Commits

Conventional-commit subjects (`feat(scope): …`, `fix(scope): …`, `docs: …`), written
for the *why*. The changelog entry and the commit should agree about what changed
and how it was verified.

### 18.3 Where to start reading

1. `iris_ai/engine.py::harness()` — the only place wiring happens, so it shows what
   depends on what.
2. `iris_ai/agent/chat.py::_build` — the graph, and then `respond`/`resume`.
3. `iris_ai/agent/tools.py::dispatch` — what a tool call actually goes through.
4. `iris_ai/toolpolicy.py` — the policy data model, if you are changing what a tool
   may do.
5. `iris_ai/kernel/` — the journal and the exactly-once boundary.
6. `iris_ai/registry.py` + `capabilities/` — the extension seams.
7. `tests/test_capability_interfaces.py` — the fastest way to see what a conforming
   implementation looks like.

Everything else is a module with one job, and its docstring says which.

### 18.4 A reset

```bash
rm -rf workspace/config/iris.json workspace/USER.md workspace/MEMORY.md workspace/memory/
```

Removes identity and memory while leaving `AGENTS.md` (your standing instructions),
skills and the code alone. Delete the index too (`config/memory.db`) and the next
boot rebuilds it from whatever Markdown is left.

---

**One sentence, if you remember nothing else:** the Markdown in `workspace/` is the
memory, the policy engine decides what may run before anything is spent, every
judgment is optional and additive, and nothing needs a server before it will say
hello.
