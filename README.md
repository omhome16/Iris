# Iris

**A personal agent harness with a visible mind.** Memory that is plain Markdown,
judgments that are typed probabilities instead of vibes, and a policy layer that
refuses *before* anything is spent — in one Python package, with a CLI, an HTTP API
and an editor adapter on top.

```bash
git clone https://github.com/omhome16/Iris.git && cd Iris
uv sync
uv run iris init        # write the config, seed a workspace, then prove it works
uv run iris chat        # talk to it
```

No Postgres. No Docker. No daemon. One command to a reply.

---

## Why it is different

Most agent frameworks give you a loop and a prompt. Iris is opinionated about the
four things that actually decide whether an assistant is useful a month later:

| | |
|---|---|
| **Memory you can read** | `MEMORY.md`, `USER.md` and daily notes are the source of truth. The index is derived and rebuildable, so nothing you care about lives only in a database. Four tiers, four provenance levels, decay, diversity, and consolidation by *dreaming* rather than by the write path |
| **Judgment, not vibes** | Every decision that is a *question over supplied text* — recall relevance, skill choice, injection screening, capture, answer sufficiency — is a typed probability from a System One model, with a deterministic fallback and a latency budget. Code owns every threshold |
| **Safe by construction** | A deterministic guard chain refuses before dispatch, approvals are bound to the digest of what they showed, untrusted content is screened, and a skill's script runs in a constructed environment through one gate |
| **Durable and observable** | An append-only turn journal gives exactly-once tool boundaries and approvals that survive a restart. Every turn leaves a trace with stage timings; every model call leaves a cost ledger |

And it is a **harness**: models, memory, judges, channels, tools, hooks and secret
stores are all Protocols behind registries, so a new integration is configuration
rather than a fork. SQLite is the default because a harness that needs a database
before it will say hello is not a five-minute harness.

## Four faces, one brain

```
  library   import iris_ai; async with iris_ai.harness() as brain: ...   <- the product
  CLI       iris init · iris chat · iris doctor · iris policy · …
  HTTP API  POST /chat, /chat/stream, /chat/resume, /mind, /costs, …
  editor    iris-acp — the same kernel, driven by Zed / JetBrains / …
```

Everything above the library is a **client**, and they share one turn pipeline, so
memory, judgments, approvals, budgets and traces behave identically whichever face
you use. There is no second brain to keep in sync.

## What `iris init` actually proves

It writes `.env` and `config/harness.toml`, seeds a neutral `workspace/`, and then
**measures** the result rather than assuming it: one no-op completion on the cheap
tier (a provider key that works, with its latency), the configured memory store
opened and counted, and the real checkpointer ladder walked so the report says which
tier a conversation would land in. Recall is reported honestly — with no embedding
provider it says **keyword-only** and names both fixes instead of printing a green
tick.

It is deliberately not interactive: everything a wizard would ask has a right answer
that can be detected. Re-running it never overwrites anything you edited.

## The CLI

Run `iris` with no arguments for the start screen; the help is generated from the
command registry, so it can never list a command that does not exist.

| | |
|---|---|
| **Start** | `init` · `chat` · `doctor` · `version` |
| **Configure** | `secrets` · `mcp` · `policy` |
| **Inspect** | `tools` · `plugins` · `guards` · `costs` · `agents` · `skills` · `cron` |
| **Maintain** | `migrate` |

Inspection commands work with **no engine running** — they read declarations and
files, so `iris policy`, `iris tools` and `iris guards` answer questions on a
machine where nothing is up. Diagnostics are cp1252-clean and readable when piped;
colour and the rounded frames are only drawn on a terminal.

## Configuration, in one breath

```
defaults  <  .env  <  config/harness.toml  <  real environment  <  CLI flag
```

`.env` holds secrets and machine-local overrides; `config/harness.toml` is the
committable manifest (channels, capability backends, ordinary settings); `.mcp.json`
declares external MCP servers, with `${VAR}` resolved through the secret store so a
token never sits in a committable file. A profile can ship its own manifest and be
selected with `HARNESS_CONFIG=…`, which is how `examples/assistant/` works.

## Docs

| | |
|---|---|
| **[`DOCS.md`](DOCS.md)** | the manual. Every page in depth: install, all 15 commands, every setting, the turn, memory, the judgment layer, safety, extending, interfaces, observability, providers, scheduling, deployment, testing, troubleshooting |
| [`CHANGELOG.md`](CHANGELOG.md) | what changed, per phase, with the numbers and the method that produced them |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | setup, the gates to run, and the house rules |
| [`examples/assistant/`](examples/assistant/README.md) | a reference profile: a persona, a workspace and a channel, with no core edits |
| [`workspace/AGENTS.md`](workspace/AGENTS.md) | the neutral operating contract a fresh workspace starts from |
| [`skills/web-page-to-notes/`](skills/web-page-to-notes/SKILL.md) | the builtin skill that proves the Agent Skills format: a stdlib-only script, no network, no environment reads |

New here? Read [`DOCS.md` §1](DOCS.md#1-what-iris-is) and
[§2](DOCS.md#2-install-and-first-run). Changing something? Go straight to
[§18.3](DOCS.md#183-where-to-start-reading).

## Requirements

| | |
|---|---|
| Python | 3.12 or 3.13 |
| A provider key | **optional to start** — without one, recall is keyword-only and model calls degrade |
| Postgres | **not needed** (SQLite is the default; pgvector is the scale-up option) |
| Docker | **not needed** (only for Postgres, the full API + bridge stack, or the `container` script sandbox) |
| Optional extras | `[acp]` for `iris-acp`, `[otel]` for OTLP spans. Absent, each degrades with a reason rather than failing the boot |

```bash
pip install iris-personal-ai            # core
pip install "iris-personal-ai[acp]"     # + the editor adapter
pip install "iris-personal-ai[otel]"    # + OTLP span export
```

## Status

Shipped and verified: **1129 tests pass offline** (the two pgvector-backed files
need a service and run in CI), `ruff` clean, and the wheel installs into a clean
venv and works with no daemon. CI proves the five-minute onboarding path with a
300-second budget, a model-free retrieval gate, a non-root image build, a
dependency audit, and byte-equality of the shipped default profile.

Deliberately deferred, and stated rather than hidden: the native turn kernel (the
journal is here; the loop swap is not), non-LangGraph orchestration modes, native
provider SDK backends, MCP-server mode, and `sqlite-vec` as the vector extension.

MIT licensed.
