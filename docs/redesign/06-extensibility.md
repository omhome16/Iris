# 06 — Extensibility: plugins, skills, hooks, config

This is the layer the current codebase already started (`registry.py`,
`hooks.py`, `channels/registry.py`, `toolregistry.py`, `manifest.py`). The
redesign generalizes it to every kind and makes the manifest the single
configuration surface.

## 1. The one registry, every kind

`Registry[T]` (shipped) is the mechanism for all of: models, tools, channels,
memory backends, judges, evaluators, orchestrators, commands. Discovery has two
sources — Python entry points (installed packages) and the manifest (config) —
and a capability is only imported/constructed when enabled.

A conformance test per kind asserts the interface holds, so a plugin cannot pass
discovery with a broken contract.

## 2. Skills (kept, aligned to the open spec)

Iris already ships Agent Skills (`SKILL.md` + frontmatter + `scripts/`). The
redesign keeps it and leans into **progressive disclosure**: at startup only the
name + description are loaded (~30–50 tokens each); the body is read only when
the skill is chosen; scripts/references load last. `SKILL.md` should stay under
~500 lines; overflow goes to `references/`. Skills are procedural memory and are
discovered from workspace, shipped builtins, extra dirs and entry points — the
existing registry, unchanged.

## 3. Hooks

The hook bus (shipped) is the only way the kernel is extended at runtime. Events:
`turn_start`, `pre_tool`, `post_tool`, `on_error`, `turn_end` (and later
`pre_model`, `post_model`, `on_approval`). A hook can observe or refuse; a
raising hook is skipped. Built-in policy registers at a negative priority so it
always runs first.

## 4. The manifest (`iris.toml`)

One file for everything a user configures; secrets stay in env. Precedence:
`defaults < .env < manifest < environment < CLI` — a real env var wins (a
container/CI override), and the manifest wins over the sample `.env` so a profile
can own `workspace_dir` without a copied default silently overriding it
(`iris_ai.PRELOADED_ENV` is what tells the two apart). Sections mirror the
capability kinds:

```toml
[kernel]
orchestrator = "react"        # react | plan-execute | delegate
profile = "assistant"         # a named bundle of the below

[models]
provider = "auto"

[memory]
backend = "sqlite"            # sqlite | postgres | memory

[tools]
disable = ["computer"]

[mcp.servers.weather]
transport = "http"
url = "https://mcp.example.com/mcp"
trust = "review"

[channels]
enabled = ["telegram"]

[safety]
default = "ask"               # for credentialed/exec/control
kill_switch = false
```

Profiles/bundles let one file ship a working setup (`profile = "assistant"`)
while a power user overrides individual keys.

## 5. Authoring a plugin (the five-minute path)

**A model backend**
```toml
[project.entry-points."iris_ai.models"] my_gateway = "pkg:GatewayBackend"
```
```python
class GatewayBackend:                     # implements ModelBackend
    name = "my_gateway"
    async def complete(self, req): ...
    async def stream(self, req): ...
    async def embed(self, texts): ...
```

**A tool provider** — `iris_ai.tools` entry point, object with `.name` and
`.tools(runtime)` (shipped). **A channel** — `iris_ai.channels` entry point
implementing `Channel` (shipped). **A hook** — register on the bus. **A skill** —
drop a `SKILL.md`. **An MCP server** — no code, just a manifest block.

No plugin edits core. Each ships its own tests; the registry's conformance test
proves it honors the contract.

## 6. What is deliberately *not* pluggable

The kernel, the manifest loader, the secret store and the trace writer. These are
the invariants the rest of the system trusts; making them swappable would make
every capability's behavior conditional on which variant is loaded.

## 7. What this replaces / keeps

| Today | Redesign |
|---|---|
| `registry.py`, `hooks.py` (shipped) | kept; more kinds registered |
| `manifest.py` + `config/harness.toml` | grows into `iris.toml` with profiles |
| `skills/` registry | kept, progressive-disclosure guidance |
| `toolregistry.py` (shipped) | kept; MCP tools join the same surface |
| scattered env settings | one manifest + env for secrets only |
