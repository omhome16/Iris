# The plug-and-play redesign — a capability-bus architecture

> Status: **implemented**. The capability registry (`iris_ai.registry`), the
> lifecycle hook bus (`iris_ai.hooks`, with the guard chain as its first
> subscriber), the Channel protocol + config-driven channel registry
> (`iris_ai.channels.registry`), the tool-provider registry
> (`iris_ai.toolregistry`), the declarative manifest (`iris_ai.manifest`), and
> the `iris plugins` inspection CLI are all shipped and covered by tests. What
> each step changed is recorded in §5.

## 1. Where we are (and what's actually good)

The engine is assembled by hand in `src/iris_ai/engine.py::harness()`: files →
ledger → LLM → JEV → index → reindexer → checkpointer → `Runtime` → `ChatGraph`
→ services. Every client (CLI, HTTP API, Telegram bridge) boots that one
function. Good bones:

- **Registries already exist, separately**: `providers.py` (model backends),
  `roles.py` (agent roles), `skills/registry.py` (skills, with entry-point
  discovery), `toolpolicy.py` + `agent/tools.py` (tools).
- **A real dependency-injection seam already exists**: `Runtime` is passed to
  everything; `ChatGraph` takes it.
- **Security/quality are ahead of most harnesses**: sandboxed file tools, a
  gated skill-script subprocess, HITL approval bound to a digest, trace content
  policy that never logs secrets, an eval lab with intervals.

So this is not a rewrite of the *brain*. Memory, JEV, eval, skills and
sandboxing work and are differentiators. The redesign is of the **shell**: how
capabilities are declared, discovered, configured and assembled.

## 2. The problem with the shell

1. **One hardcoded integration.** `channels/telegram_mcp.py` is a single client
   for a single MCP URL; `_start_services` constructs it literally. Adding
   Discord or a second MCP server means editing core.
2. **No generic hook system.** The pre-tool guard chain is an inline special
   case in the agent loop. There is no post-tool, no on-error, no turn-start/end
   seam — Claude Code exposes ~28 lifecycle events; Iris exposes one, privately.
3. **Assembly is code, not config.** Which channels start, which services run,
   what is enabled lives in `harness()`'s body and `Settings`, not in a
   declarative manifest a user can edit.
4. **Each extension point re-invents its onboarding.** Providers, roles and
   skills each discovered/validated differently. There is no one contract.

## 3. Target architecture — three ideas

### 3.1 One generic registry per capability *kind* (`iris_ai/registry.py`)

```python
reg: Registry[Channel] = Registry("channel")
reg.register("telegram", TelegramChannel, source="core")
reg.discover("iris_ai.channels")            # installed packages
reg.register("slack", SlackChannel, enabled=False, source="config")
channel = reg.build("slack", token=...)     # factories run only when enabled
```

- Duplicate names are explicit conflicts naming both sources.
- Unknown names fail with the known list.
- `discover()` registers entry points **without calling them** — a disabled
  integration is never imported, let alone constructed (efficiency goal).
- `enabled()` / `register_value()` let core own a default and config override it.

### 3.2 A typed Protocol per extension point

Each "part" is a `typing.Protocol` with a conformance test, so a plugin is
correct by construction and core can accept it blind:

| Kind | Protocol | Replaces | Notes |
|---|---|---|---|
| `model` | `ModelBackend` | provider table | formalize `providers.py` behind it |
| `channel` | `Channel` (`send`, `send_photo`, `inbound`, `close`) | `TelegramMCPClient` | MCP-generic; one MCP client, N specs |
| `tool` | `ToolProvider` (`tools(runtime) -> list[Tool]`) | hardcoded `get_tools` list | a plugin can add tools |
| `hook` | `Hook` (subscribes to `HookBus` events) | inline guard chain | pre/post/on-error/turn events |
| `memory` | `MemoryBackend` (`search`, `escalate`, `index`, `stats`) | `MemoryIndex` | swap pgvector for sqlite/memory |
| `agent` | `Role` (already exists) | `roles.py` | unchanged |

### 3.3 A declarative manifest

`config/harness.toml` (non-secret) with env override for secrets:

```toml
[channels.telegram]
enabled = true
url_env = "TELEGRAM_MCP_URL"          # secrets/URLs stay in env

[channels.discord]
enabled = false

[tools]
disable = ["computer"]                # opt-out without code

[hooks]
# a package can supply a hook; core ships the guard chain
[budgets]
per_turn_tokens = 60000
```

Precedence: **defaults < `.env` < manifest < environment < CLI flag**. One
loader, one validator. `iris doctor` reports exactly what resolved and why.
(`.env` sits below the manifest because it is where a copied sample's defaults
live — `WORKSPACE_DIR=./workspace` there must not defeat a profile that names its
own workspace. Only a *real* environment variable, one that was set before Iris
loaded anything, outranks the file: `litellm` calls `load_dotenv()` on import,
which would otherwise make the two indistinguishable. `iris_ai.PRELOADED_ENV`.)

## 4. What a contributor does (the whole point)

### Add a new channel (Discord), new design
1. `pip install iris-discord` — it advertises
   `[project.entry-points."iris_ai.channels"] discord = "iris_discord:Channel"`.
2. In `config/harness.toml`: `[channels.discord] enabled = true`,
   and set `DISCORD_BOT_TOKEN` in `.env`.
3. Run `iris channels list` / `iris doctor` to confirm.

**No core file changes.** Discovery, validation, enable/disable and construction
are all the registry's job.

### Add a new tool provider
One module exposing `ToolProvider` and one entry point under `iris_ai.tools`.
The tool appears in `iris tools`, is subject to the existing `toolpolicy`
classes, the guard chain and the trace — inherited for free.

### Add a new model backend
One `Provider` table entry (already the pattern) plus `Settings` fields;
`test_providers.py` and `test_packaging.py` already fail if you forget one.

## 5. Migration sequence (never leaves the repo broken)

Each step was additive, behavior-preserving, and landed with tests green.

1. **Foundation** ✅ `registry.py` + `hooks.py` + contract tests.
2. **Hooks** ✅ The guard chain is now the first `HookBus` subscriber; the loop
   emits `pre_tool`/`post_tool`/`on_error`/`turn_start`/`turn_end`, and the
   chain's verdict still refuses before dispatch. Guard tests unchanged.
3. **Channels** ✅ `Channel` protocol + `channels/registry.py`; the boot path
   discovers plugins, resolves the enabled set from config, and connects each
   channel independently with a background retry for whatever did not come up.
4. **Tools** ✅ `toolregistry.py` + a `ToolProvider` provider; core tools are the
   `core` provider, and installed packages add more via the `iris_ai.tools`
   entry point. `TOOL_NAMES` still gates the core set only.
5. **Assembly** ✅ `harness()` applies the manifest, discovers capabilities, and
   boots channels through the registry. The manifest path (`config/harness.toml`)
   layers under the environment, so env always wins.
6. **CLI + docs** ✅ `iris plugins channels|tools|hooks`; this doc, plus the
   channel/tool/hook recipes in `docs/extending.md`.

## 6. Meeting the five goals

| Goal | How the redesign serves it |
|---|---|
| **Easy to handle** | A plugin is one module + one entry-point line + one manifest stanza. Setup stays `uv sync && iris chat`. |
| **Fast** | Factories run only for enabled capabilities; discovery never imports disabled plugins; independent boot steps can run concurrently; no sync I/O on hot paths. |
| **Easy to modify** | Transport (channel), policy (hook), capability (tool), model (backend) and memory live behind separate Protocols. Changing one cannot force touching another. |
| **Customizable part by part** | That is the registry: any kind is independently swappable, and `replace=True` lets a user override a core default deliberately. |
| **Config-driven** | The manifest + env precedence means enabling/disabling capabilities, tools and budgets is configuration, not source edits. |

## 7. Tradeoffs (stated, not hidden)

- **Indirection cost.** A registry adds one module and one lookup layer per
  capability. For a single-user bot that is overkill; for an OSS harness meant
  to be configured many ways, it is the product. Accepted.
- **Two config surfaces** (env for secrets, TOML for structure). Mitigated by a
  single loader with explicit precedence and a `doctor` readout.
- **Entry-point import cost.** Mitigated by lazy loading — discovery registers a
  callable, it does not import.
- **Don't rewrite the brain.** Memory, JEV, eval and sandbox stay. Rewriting
  working, differentiated subsystems would trade proven behavior for churn.

## 8. Efficiency baseline (do not regress)

- Boot already skips work it doesn't need (computer-use is not imported unless
  enabled; reindex is skipped in degraded mode). The registry extends that rule
  to every capability.
- The reply path stays free of synchronous I/O; hooks are awaited on the same
  loop and a raising hook is skipped.
- Any new boot step that can run concurrently with another must not serialize
  on it (the checkpointer and index already connect around the same window).
