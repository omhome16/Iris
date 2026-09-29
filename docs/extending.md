# Extending Iris

Recipes for the changes people actually make. Each one lists the files, the test
that will fail if you get it wrong, and how to verify. Read
[`docs/architecture.md`](architecture.md) first if you want the map; this page
assumes you know roughly where things live.

**The house rule:** every extension lands with a test that would fail if the
behaviour were removed, and nothing is relaxed to get green. If a change makes an
existing test awkward, that test is either wrong (fix it and say so in the
commit) or the change is.

## Verify your work

```bash
uv run ruff check .                                            # lint (a gate, not a suggestion)
uv run pytest tests -q --ignore=tests/test_memory_pipeline.py \
                       --ignore=tests/test_retrieval_gate.py   # fast, deterministic, no DB
uv run pytest tests -q                                         # everything, needs the pgvector DB
docker compose up -d postgres                                  # the database the line above wants
uv run iris doctor                                             # environment sanity, names only
uv run iris --help                                             # the surface you just changed
```

The suite is split by what it needs: everything except `tests/test_memory_pipeline.py`
and `tests/test_retrieval_gate.py` runs with no database and no network. Those two
files need a real pgvector service, and they **fail loudly** rather than skipping —
see [`docs/support.md`](support.md).

---

## Add a tool

A tool is five things: an implementation, an entry in `TOOL_NAMES`, a **class
declaration**, a **namespace**, and a test. The class is what derives its policy —
`allow` / `ask` / `deny` — and the namespace is what keeps the tool reachable once
its schema is deferred, so neither is optional metadata.

1. **Implement it and register it in `build_tools`** in
   `src/iris_ai/agent/tools.py` (or in the module that owns the capability, as
   `iris/computer/` does). Registration is a `Tool(name, description, params,
   handler)`; a handler that returns a JSON string.
2. **Add its name to `TOOL_NAMES`** in the same file. `get_tools` asserts the real
   registry is a subset of it, so a tool nobody declared fails loudly rather than
   shipping unclassifiable.
3. **Declare its class and surface** in `src/iris_ai/toolpolicy.py::TOOL_DECLARATIONS`:

   ```python
   TOOL_DECLARATIONS["send_postcard"] = Declaration(
       cls=ToolClass.DELIVERY, surface="extended",
   )
   ```

   | Class | Default policy | Use for |
   |---|---|---|
   | `read` | `allow` | memory search, traces, stats |
   | `filesystem` | `allow` | sandboxed file tools |
   | `memory_write` | `allow` | `remember`, `forget`, `note` |
   | `network` | `allow` | `web_search`, `ingest_url` |
   | `credentialed` | `ask` | anything using an API key |
   | `delivery` | `allow` | `send_message`, `send_photo` |
   | `control` | `ask` | `computer` |
   | `external` | `ask` | a tool that arrived from outside core (MCP, a plugin) — its own source's verdict is what decides; see below |

   `surface="core"` (the default) means never deferred. `"extended"` means the
   tool can be pushed off the visible list when the budget binds, and is then
   reachable through `find_tools` — declare it at the point in the table where you
   want it to disappear *last*.
4. **Put it in a namespace** in `NAMESPACES`. A tool in no namespace is dropped
   from the catalog the prompt gets for the deferred remainder, which is how a
   capability quietly becomes one the model never reaches for. Open a new group
   with a one-line `NAMESPACE_PURPOSE` entry if none fits. If your tool reaches
   outside the sandbox, consider whether it belongs in `NON_OWNER_BLOCKED` too —
   a scheduled task must not write durable memory.
5. **Run the coverage tests** — `tests/test_tool_policy.py` fails if you forgot
   the declaration or declared a tool that does not exist,
   `tests/test_tool_loading.py` fails if the namespace table no longer partitions
   the declarations, and `tests/test_cli.py` fails if any new CLI text is not
   cp1252-clean.

Read this before choosing `allow`: the class default applies to everything in it,
and **`deny` beats every override**, including a class-wide deny that a per-tool
`allow` tries to re-open. `iris tools` shows the resolved policy and where it came
from.

**Refusals are not errors.** Return a JSON string with `ok: false` and a reason
the model can act on ("lane='escalate' searches daily notes instead"), because a
tool that answers only "no" teaches the model to retry.

### Add a tool *from outside core* (no core edit)

To ship tools in a separate package without touching `agent/tools.py`, expose an
object with `name` and `tools(runtime) -> list[Tool]` under the `iris_ai.tools`
entry-point group:

```toml
# in the plugin package's pyproject.toml
[project.entry-points."iris_ai.tools"]
my_tools = "my_package:MyTools"
```

```python
class MyTools:
    name = "my_tools"
    def tools(self, runtime):
        return [Tool("weather", "Look up the weather.", {"type": "object", "properties": {}}, handler)]
```

The engine discovers it at boot (`discover_tool_providers()`). Plugin tools are
**additive**: they are not validated against `TOOL_NAMES` (that closed set exists
so a *skill manifest* cannot name a tool that does not exist), and are logged when
added. `iris plugins tools` lists every provider and where it came from.

### Add a capability from an MCP server

The most common way to add tools is not to write any code: point Iris at a
server that already exists. Servers are **declared** in `.mcp.json` (the file
shape other MCP clients use, so a config you already have works), and are
connected once at boot.

```bash
cp config/mcp.json.example .mcp.json     # then edit it
uv run iris plugins mcp                  # declared, their trust, and each tool's policy
uv run iris plugins mcp --live           # connect and list what they actually offer
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

The transport is inferred — `url` means HTTP (streamable, or `sse` if you say so),
`command` means a child process — and `${VAR}` is resolved from the environment, so
a token never has to sit in a committable file. A malformed declaration is a
**boot error** naming the server and the key: a server you believe is connected
but is not is worse than a startup failure.

**Trust belongs to the server, not the tool.** `policy_for` turns the server's
stated trust plus the tool's own `read_only_hint` into one verdict:

| trust | read-only hint | policy |
|---|---|---|
| any | yes | `allow` — it cannot write, by its own account |
| `owner` | no | `ask` — a human approves before it changes anything |
| `untrusted` (default) | no | `deny` |

`deny` is a **floor**: `approval: "never"` cannot re-open an untrusted write,
because "deny wins" is the one rule `toolpolicy` refuses to bend. Per-server
`approval: "always"` means every call, reads included. Your own overrides still
apply on top: `tool_policy_overrides="wiki/delete=deny,external=deny"` works by
tool name or for every external tool at once.

Names are namespaced `server/tool`, so two servers may both ship `search`. The
implementation is small and worth reading before you extend it:

| file | what it decides |
|---|---|
| `src/iris_ai/mcp/__init__.py` | the declaration format, `${VAR}`, and `policy_for` |
| `src/iris_ai/mcp/client.py` | one connection: connect, list, call; `McpUnavailable` |
| `src/iris_ai/mcp/provider.py` | the pool, the declarations, approval, retry |

**An untrusted server's output is screened, not trusted.** A `review` or
`untrusted` server's reply is run through the injection guard before the model
reads it: a block withholds the text entirely (`ok: false`, `withheld: true`, no
`text`), and every reply that passes is tagged as untrusted data. With no judge
available it is tagged ``screened: false`` — reported as unchecked, never passed
off as checked.

Things that are **deliberately not done**, so nobody assumes otherwise: `ws://` is
refused because this SDK ships no websocket client transport, rather than
silently downgraded to another transport; and a session that *cannot ask* (a
scheduled task, a cron heartbeat) is refused before a tool whose policy is `ask`
can reach an interrupt nobody can answer, because pausing forever is not a
refusal.

**stdio on Windows.** The stdio transport spawns the server with asyncio
subprocesses, and the selector event loop that `iris chat`/`iris api` select on
Windows does not implement them — the same wall `skills/runner.py` hit, which is
why skill scripts run on a worker thread. A stdio server declared there is
refused **with that explanation** instead of failing deep inside the SDK. Run the
server yourself and use `url`.

---

## Add a skill

Skills are procedural memory. Two encodings are supported and both are discovered
automatically: the flat sidecar pair Iris writes herself
(`workspace/skills/<name>.json` + `.md`) and the open **Agent Skills** layout
(`<name>/SKILL.md`, <https://agentskills.io/specification>) that shipped builtins
use.

```bash
mkdir -p skills/my-skill/scripts
cat > skills/my-skill/SKILL.md <<'MD'
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
MD
uv run iris skills validate     # exits 1 on errors
uv run iris skills show my-skill
```

| Rule | Why |
|---|---|
| `allowed-tools` **narrows** the turn while the skill is active | a skill can never re-open what the session closed |
| an unknown tool in `allowed-tools` is a validation **error** | a manifest that lies about the surface is worse than a missing one |
| script code runs only through `skill_run` | one gate, one audit trail, one place to reason about |
| `scripts/` gets a JEV safety judgment before it runs, and the owner approves it | see `docs/jev.md` §3 #4 |

Scripts run in a constructed environment (no `.env`, no provider keys) as the same
OS user. That is **process** isolation, not kernel isolation — read the residual
risk note in [`docs/deployment.md`](deployment.md) before shipping someone else's
code.

---

## Add a client (something that talks *to* Iris)

A client can feed Iris a message and show the reply. Do not add a second turn
pipeline: implement the contract in `src/iris_ai/channels/brain.py::BrainClient`
(`respond`, `resume`, `stream`, `json_get`, `json_post`) or reuse
`HttpBrainClient` and point it at the core.

- `mcp_servers/telegram/` is the worked example over HTTP.
- `src/iris_ai/cli/chat.py` is the worked example in-process.

**Idempotency is the client's job.** Telegram redelivers updates; the bridge
keeps `UpdateLedger` for exactly that. If your transport can deliver a message
twice, deduplicate before you call `respond`, or the owner's memory gets the same
fact twice.

The auth header comes from `iris_ai.security.auth_headers(token)` — one definition,
shared with the server-side check, so a client and a server cannot disagree about
the scheme.

## Add a channel (a transport Iris reaches) — plug and play

A *channel* is an outbound transport (Telegram over MCP today; Discord, Slack, a
webhook later). It is registered in the channel registry, not wired in by hand, so
a new one is **configuration, never a core edit**.

**In-tree:**

1. Implement the `Channel` protocol (`src/iris_ai/channels/base.py`): `name`,
   `connected`, `connect`, `close`, `send_message`, `send_photo`,
   `get_chat_history`.
2. Register it: `CHANNELS.register("discord", DiscordChannel, source="core")` in
   `src/iris_ai/channels/registry.py`.

**Out of tree (the plug-and-play path):** ship a package exposing

```toml
[project.entry-points."iris_ai.channels"]
discord = "iris_discord:DiscordChannel"
```

Then, either way, enable it in config — no code change:

```bash
CHANNELS_ENABLED=telegram,discord          # or in config/harness.toml
CHANNELS_DISABLED=                          # deny always wins
CHANNEL_DISCORD_URL=https://…               # per-channel address override
```

The engine discovers plugins, resolves the enabled set, and connects each channel
independently — a down channel is skipped, never fatal, and retried in the
background. `iris plugins channels` shows what is registered, enabled, and where
each came from.

---

## Add a role (a specialist)

Roles are declared, not prompt-pasted. Add one in `src/iris_ai/agents/roles.py` with
its bounds explicit: tier, recall lane, tool allowlist, round cap, output cap.
Then:

- `iris agents roles` shows it with the bounds that shape it;
- `Orchestrator` enforces the caps in code — an over-long answer is truncated, a
  run past its deadline is cut, and the delegation is recorded as a **typed
  `Handoff`** with provenance (which claims are sourced, and which are not);
- the fan-out decision itself is a JEV judgment (`iris/jev/agents.py`), not a
  regex, so a new role inherits it.

Keep the read-only default: a research role gets `READ_ONLY_TOOLS` and cannot
write memory. Widening that is a deliberate change with a test.

---

## Add a guard

A guard is a function that can only ever **refuse**, and it runs before dispatch:

```
budget → circuit → spiral/dedup → context → record
```

1. Add the state to `src/iris_ai/guards.py` (see `SpiralDetector` for the shape: a
   `note()` that returns a `Verdict`, and a `reset()` that clears turn-scoped
   state only).
2. Call it from `GuardChain.before()` **in that order** and return
   `Verdict(False, GuardName.YOURS, reason, detail)`.
3. Give the reason a next step. The chain's whole point is that a refusal is
   actionable.
4. Record it — `GuardChain.record()` already writes every refusal to the turn
   trace, and `iris guards` / `GET /guards` read the snapshot.

Its tests belong next to the others in `tests/test_guards.py` (unit) and
`tests/test_guard_wiring.py` (a refused call **never reaches `dispatch`** — assert
that, not that a message was printed).

Everything in the chain must be deterministic and model-free. A guard that needs
a model to decide whether to spend money can itself run away.

### Where a guard plugs in: the hook bus

The chain is no longer a special case in the agent loop — it is the first
subscriber on the lifecycle bus (`src/iris_ai/hooks.py`). A turn emits
`turn_start`, `pre_tool`, `post_tool`, `on_error`, `turn_end`; each hook may
observe or refuse, hooks run in priority order, and **a hook that raises is
logged and skipped** so telemetry never costs a reply. Iris's own guards register
at priority `-100`, so built-in policy always runs before an add-on.

To add an audit/instrumentation/policy hook, register on the bus the graph builds
(`ChatGraph.hooks`), or pass one in via `runtime.hooks`. `iris plugins hooks`
lists the events and their built-in subscribers.

---

## Change a safety knob

Three knobs, and one rule for all of them: **a policy you cannot read is a policy
you cannot check.**

```bash
uv run iris policy              # classes, overrides, and every declared server
uv run iris policy classes      # what each class defaults to (and what moved it)
uv run iris policy overrides    # what each override applies to — typos included
uv run iris policy servers      # the rule each MCP server's tools would get
uv run iris secrets             # which secrets are missing, and where each is found
```

**`TOOL_POLICY_OVERRIDES`** (`"send_message=deny,external=deny"`) may only ever
*tighten*: `deny` wins at every level, so nothing here can re-open what a class or
a server's trust closed. A key that names no tool and no class changes nothing and
is *reported* — a typo in a security knob must be visible rather than silent.

**`SECRET_STORE`** chooses where a secret lives: `auto` (the OS keychain when the
optional extra is installed, else a 0600 file), `env` (read-only through Iris),
`keyring`, `file`. Two rules the module enforces rather than documents: the
`env` backend refuses to *write* (a secret in the process environment is inherited
by every child process, including a skill's script), and the `file` backend says
**NOT encrypted** in its own location string instead of letting you assume
the keychain. `${VAR}` in a declared MCP server resolves from the environment
first, then the store — and only in a real load, so a test that supplies its own
environment never sees your stored secrets.

**`EXEC_SANDBOX`** (`process` | `container`) is the isolation level for a skill's
script. `process` is the default and needs nothing installed. `container` adds
`--network none`, a read-only root, memory/PID caps and a `:ro` skill mount, and
**fails closed** when the runtime is missing. There is no `in_process` level: a
key that can turn a boundary off eventually will.

### Add a secret-store backend

Same shape as every other capability — register it and select it by name:

```toml
[project.entry-points."iris_ai.secret_stores"]
vault = "my_package:VaultStore"
```

The object needs `name`, `get`, `set`, `delete` and `location()` (which must say
what protection the store actually gives). `iris_ai.secrets.SECRET_STORES` holds
the built-ins; a store that raises `SecretStoreError` with an actionable message
beats one that returns `None` and looks like "no such secret".

## Add an eval metric or a judgment

**A metric for the lab** (`scripts/eval_lab.py`): add it to the per-query
outcomes and report it through `iris_ai.eval.stats` — a rate gets `wilson_interval`,
a mean gets `bootstrap_mean_ci`, and a comparison gets `paired_difference_ci`, so
query difficulty cancels. Then register it in the pre-registered `DecisionRule`
before you run it. A point estimate with no interval is not a measurement, and a
rule chosen after seeing the numbers is how every ablation "wins".

**A judgment with JEV** — the question to ask yourself is *"is this a decision
about supplied text?"*. If it is, JEV should make it (see `docs/jev.md` §3.0 for
the audit of every model call). Follow the shape of
`src/iris_ai/jev/recall.py`: build one `state` plus typed questions, send **one
batched request**, fall back to the existing deterministic path when `ask()`
returns `None`, and record the probabilities in the turn trace.

If the answer is generation — a summary, a header, a rewritten reply — it stays
with the LLM, and it should say so in the docstring the way
`iris/jev/client.py` does.

---

## Change a setting

1. Add the field to `src/iris_ai/config.py` with a comment explaining *why* it
   exists, not what it is.
2. Add it to `.env.example` (a test fails if you forget — every setting must be
   documented, and every key there must name a real setting).
3. Add it to the relevant section of [`docs/deployment.md`](deployment.md) if an
   operator has to make a decision about it.
4. Prefer a default that preserves today's behaviour, and `0`/empty meaning
   "no limit" rather than "limit of zero".

---

## House conventions

- **Comments explain why.** "Sort by score" is noise; "sorting by score keeps the
  cheap tier from outranking a source the owner pinned" is the reason the next
  person needs.
- **Docstrings state the contract and the degradation.** Every optional layer says
  what happens when it is absent.
- **`from __future__ import annotations`**, `slots=True` dataclasses, `StrEnum`
  for closed vocabularies, and no new dependency without a reason in the commit
  message.
- **Errors are values where the caller can act**, exceptions where it cannot.
- **Never print a secret value.** `iris doctor` prints names and `set`/`missing`.
- **Tests are named for the invariant**, not the function: `test_deny_beats_a_class_allow`,
  not `test_resolve_2`.
