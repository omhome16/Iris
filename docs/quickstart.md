# Quickstart — clone to a reply in five minutes

The claim this page makes is measured, not asserted: CI job `onboarding` runs the
install-and-init half of the steps below on a clean checkout, asserts a reply with
`--offline` (no provider key in CI, and **no Postgres service in the job**), times
the whole thing, and fails if it takes more than 300 seconds
(`.github/workflows/ci.yml`).

## What you need

| | |
|---|---|
| Python | 3.12 or 3.13 |
| [uv](https://docs.astral.sh/uv/) | for a checkout (`pip install uv`) |
| A provider key | **optional to start.** Without one, recall is keyword-only and model calls degrade — the harness boots and answers deterministically |
| Docker | **not needed.** It is for Postgres (larger corpora) and the full API + bridge stack |
| Postgres | **not needed.** The default memory backend is one SQLite file, and threads fall back to a second one |

## The path

```bash
git clone https://github.com/omhome16/Iris.git && cd Iris
uv sync                      # install from the lockfile
uv run iris init             # write .env + config/harness.toml, then prove it
uv run iris chat             # talk to it
```

Then put one provider key in the `.env` that `iris init` wrote, and start the
next session with it. That is the whole setup.

### What `iris init` actually did

| File | |
|---|---|
| `.env` | copied from `.env.example`; **never overwritten** without `--force` |
| `config/harness.toml` | copied from the example — the declarative config a boot reads |
| `workspace/AGENTS.md` | the neutral operating contract, written only if the workspace has none |
| `workspace/README.md` | what each file in a workspace is for |

…and then it **measured** the result rather than assuming it:

| Probe | What it proves |
|---|---|
| one no-op completion on the cheap tier | a provider key that works, reported with its latency |
| the configured memory store opened for `stats()` | the no-service claim holds, and the chunk count is real |
| the real checkpointer ladder walked | which tier a conversation would land in (`postgres` → `sqlite` → in-memory, with a warning when it is the last one) |

Recall is reported separately and honestly: with no embedding provider the report
says **keyword-only**, names the fix (`GEMINI_API_KEY`, the default embedding
model) and the keyless alternative (`LLM_PROVIDER=ollama` with
`ollama/nomic-embed-text`). A working install that under-reports is worse than a
warning — so it is a warning.

`iris init` is deliberately **not interactive**: everything a wizard would ask
has a right answer that can be detected, and a prompt would only make this page
slower and the command untestable. `--offline` skips the two live probes (CI and
pre-commit use it); `iris doctor` re-checks the environment any time.

## Talk to it

```bash
uv run iris chat                          # streaming; same pipeline as the API
uv run iris chat --once "summarize my notes"
uv run iris chat --session work           # a separate thread (memory continuity per session)
```

First contact runs the onboarding wizard — name, tone, timezone — and writes
`workspace/USER.md` and `workspace/config/iris.json`. Nothing about identity is
hard-coded; a fresh workspace has no name until it is told one.

## Verify the pieces yourself

```bash
uv run iris doctor        # environment: providers, store, recall, checkpointer
uv run iris plugins       # which channels, tools, hooks and MCP servers are live
uv run iris costs         # what the model calls cost, from the ledger
uv run pytest tests -q    # the suite (the two Postgres files fail loudly without a DB)
uv run ruff check .       # lint is a gate, not a suggestion
```

## Next

| You want | Read |
|---|---|
| a real agent (persona, persona files, channels) | [`examples/assistant/`](../examples/assistant/README.md) |
| to install a plugin, or write one | [`docs/plugins.md`](plugins.md) |
| to add a tool / skill / channel to core | [`docs/extending.md`](extending.md) |
| Postgres, bigger corpora | `MEMORY_BACKEND=pgvector` + `POSTGRES_DSN` in `.env`, then `iris migrate --to pgvector` |
| to run it as a service | [`docs/deployment.md`](deployment.md) |
| an editor (Zed, JetBrains) | [`docs/acp.md`](acp.md) |
| telemetry in your own backend | [`docs/observability.md`](observability.md) |
| what CI proves, and where | [`docs/support.md`](support.md) |

## When it does not work

| Symptom | Cause and fix |
|---|---|
| `recall: keyword-only` | no embedding provider. Either a key, or `LLM_PROVIDER=ollama` with a local model |
| `model check: fail` | no usable provider key (or `LLM_PROVIDER` names one you have no key for). `iris doctor` prints the key **names** it found |
| `threads: in-memory` | neither Postgres nor the SQLite path was usable; threads will not survive exit. Check `CHECKPOINTER_PATH` is writable |
| `model not found` / provider errors on a first turn | the model name in `.env` does not exist for that provider; `iris doctor` lists the resolved provider |
| Commands exit non-zero after `init` | the report's last line is the reason; `1` means a `fail`, not a `warn` |
