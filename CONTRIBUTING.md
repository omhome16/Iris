# Contributing to Iris

Iris is a personal AI assistant built as a **library** (`import iris`) with a thin
**CLI** and a Telegram bridge as its clients. This file is how to get a change
merged without guessing at the conventions.

New here? Read [`docs/architecture.md`](docs/architecture.md) for the map, then
[`docs/extending.md`](docs/extending.md) for recipes. The design history and the
phase-by-phase plan are in [`docs/blueprint.md`](docs/blueprint.md).

## Setup

```bash
git clone <your fork> && cd Iris
uv sync                                  # uses uv.lock, installs dev tools too
cp .env.example .env                     # then fill in one provider key
uv run iris doctor                       # checks names only, never values
```

Python **3.12+**. `uv` is the package manager (`uv.lock` is committed) — not pip,
not poetry.

For the full test suite you need a real Postgres with the `vector` extension,
because five tests exercise the actual index:

```bash
docker compose up -d postgres
docker compose exec postgres psql -U iris -d iris -c 'CREATE DATABASE iris_test;'
uv run pytest tests -q
```

A plain `postgres` image will not do: the index uses `vector` columns and an HNSW
index, so the pgvector image is a hard requirement (see
[`docs/support.md`](docs/support.md)).

## Verify

```bash
uv run ruff check .                                             # lint — CI fails on this
uv run pytest tests -q --ignore=tests/test_memory_pipeline.py    # fast: no DB, no network
uv run pytest tests -q                                           # everything, needs Postgres
uv run iris --help                                               # if you touched the CLI
```

The fast suite is genuinely offline: **no API calls, no database, no network**.
If your change makes it need one, it belongs in `tests/test_memory_pipeline.py`
instead — and if a test reaches a real provider, that is a bug in the test, not a
convenience.

## What a good change looks like

1. **It comes with a test that fails without it.** Name the test for the
   invariant it protects (`test_deny_beats_a_class_allow`), not the function it
   calls.
2. **It does not weaken an existing test to get green.** Changing a test is fine
   and often right — say *why* in the commit message, as
   `tests/test_cli.py` does when the CLI grows a command.
3. **It states its degradation.** Iris runs with no TypeSafe key, no Postgres, no
   Telegram token and no Playwright. Anything optional says what happens when it
   is absent, and the answer is "works, with a named fallback", never "crashes".
4. **It keeps secrets off disk and off the screen.** `iris doctor` prints key
   *names*. Tool arguments go to the trace as a hash. If you add logging, ask
   whether a credential can reach it.
5. **It explains *why* in comments.** The interesting comments in this codebase
   record a decision and its trade-off, not a restatement of the code.

## Commits

Conventional-commit prefixes, with a subject that says what changed and why:

```
feat(guards): refuse a spiral before dispatch, with the reason it refused
fix(reflection): stop a test double from reaching the provider
docs(extending): recipes for tools, skills, channels, roles and guards
```

Keep the body to a short paragraph when the change is not obvious. A commit that
changes behaviour should say what the new behaviour is and what it replaced.

## Adding things

| I want to… | Start here |
|---|---|
| add a tool | `src/iris/toolpolicy.py` (the class declaration) + `src/iris/agent/tools.py` |
| add a skill | `skills/<name>/SKILL.md`, then `uv run iris skills validate` |
| add a channel | `src/iris/channels/brain.py` (`BrainClient`); the bridge is the worked example |
| add a specialist | `src/iris/agents/roles.py`, with its bounds explicit |
| add a guard | `src/iris/guards.py` (it can only refuse) |
| add a setting | `src/iris/config.py` **and** `.env.example` (a test enforces both directions) |
| add an eval metric | `iris/eval/stats.py` — with an interval, not a point estimate |

[`docs/extending.md`](docs/extending.md) has the detailed version of each of
these, including the test that will catch you.

## Project rules

- **No commits unless asked.** In a session with the agent, commits happen only on
  an explicit request.
- **Lint is a gate.** `ruff check .` clean, always.
- **Tests are not deleted to make a build pass.** Relocate and rename them if the
  code moved, and record the test count change in `CHANGELOG.md`.
- **A number in the docs names the method that produced it.** "684 passed, ruff
  clean" with the command beside it — not "well tested".
- **Do not add a dependency casually.** `uv.lock` is part of the review surface;
  say what the dependency buys.
- **Windows is a supported dev platform.** Console output must be cp1252-editable
  (a test enforces it), and async subprocess work goes through the selector-loop
  notes in `iris/skills/runner.py`.

## Documentation

| File | What belongs there |
|---|---|
| `README.md` | pitch, status table, quickstart, how each phase changed things |
| `docs/blueprint.md` | phase scope, gates, the risk register |
| `docs/architecture.md` | module map, turn lifecycle, invariants, where state lives |
| `docs/extending.md` | recipes for extension points |
| `docs/jev.md` | the judgment layer, every integration, the audit of model calls |
| `docs/deployment.md` | hosting, volumes, secrets, guards/budgets, backups |
| `docs/support.md` | support matrix, what CI verifies, how a release is cut |
| `CHANGELOG.md` | per-phase notes, newest first, numbers grounded in a command |
| `docs/superpowers/` | specs, plans and per-phase execution logs |

If you change behaviour, the README's status/“what changed” prose and the
CHANGELOG entry are part of the change, not a follow-up.
