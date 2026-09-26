# P2 Execution Progress

**Plan:** `docs/superpowers/plans/2026-09-23-p2-core-brain-chat.md`
**Spec:** `docs/superpowers/specs/2026-09-23-p2-core-brain-chat-design.md` (owner-approved 2026-09-23)
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-23
**Rules:** No commits unless the owner explicitly asks. No P3 work until the owner
verifies this phase's DoD.

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline (ruff + suite before touching anything) | done | `ruff` clean; 234 passed with the DB suite excluded |
| 1 | `NullIndex` + `MemoryUnavailable` (TDD) | done | `src/iris/memory/null_index.py`, exception in `memory/index.py`; `tests/test_null_index.py` (3) — written failing first |
| 2 | `iris.harness()` — one boot path | done | `src/iris/engine.py` + `tests/test_harness.py` (7); `api.py` lifespan now delegates with `postgres="require"`; whole existing suite unchanged and green |
| 3 | `iris chat` (REPL, `--once`, sessions, HITL, degraded banner) | done | `src/iris/cli/chat.py`, command in `cli/main.py`; `tests/test_chat_cli.py` (11); `tests/test_cli.py` updated to assert the registry is exactly `{chat, doctor, version}` |
| 4 | Docs: README, CHANGELOG, blueprint, learning vault | done | README status + CLI table + quickstart + degraded-mode table; CHANGELOG P2 section; blueprint P2 marked shipped; learning vault gained `Part X` and Parts I–II were corrected |
| 5 | DoD verification (hand to owner) | **awaiting owner ticks** | evidence below |

## Verification log

All commands from the repo root, 2026-09-23.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | `255 passed, 1 warning` (234 → 255: +7 harness, +11 chat CLI, +3 null index) |
| `uv run pytest tests --collect-only -q` | `260 tests collected` (the 5 pgvector tests still run in CI) |
| `uv run iris --help` | commands are exactly `chat`, `version`, `doctor` |
| `uv run iris chat --help` | `--session` (default `cli`), `--once` documented |
| `uv run iris chat --once "Reply with exactly the word: PONG"` | **real provider call**, exit `0`, output `iris> PONG`; degraded banner on stderr (no Postgres in this environment) |
| Degraded evidence check | the same turn appended a digest line to `workspace/memory/2026-09-23.md` and a trace row with `"session_id": "cli"` to `config/traces.jsonl` — a degraded session still leaves the evidence a full one does |
| `python -c "import iris; iris.harness; iris.Harness"` | lazy re-export works; `import iris` stays free of LangGraph/asyncpg |
| `uv run pytest tests -q` | 255 passed + the 5 DB tests failing loudly (by design) without Postgres; they run in CI |

## DoD checklist (owner must tick)

- [x] `uv run iris --help` lists `chat` + `version` + `doctor`, nothing fake
- [x] `uv run iris chat --help` documents `--session` / `--once`
- [x] `uv run iris chat --once "…"` runs a turn and prints a reply
- [x] `uv run iris chat` REPL: streams, `/help`, `/exit`, Ctrl+C/EOF are clean
- [x] `async with iris.harness() as brain: await brain.respond(…)` works (library example in the README)
- [x] Degraded mode is announced and recall says why it is unavailable
- [x] `uv run ruff check .` clean
- [x] `uv run pytest -q` green apart from the 5 pgvector tests (CI runs all 260)
- [ ] **Owner confirmation** — the gate for P3

## Deviations from the plan (with reasons)

1. **The module is `iris/engine.py`, not `iris/harness.py`.** A submodule and a
   package attribute share one namespace: `import iris.harness` would set the
   package attribute to the *module* and silently clobber the `harness()`
   callable the public API promises. The implementation lives in `engine.py`;
   `iris.harness()` / `iris.Harness` are the public names, re-exported lazily.
2. **The degraded banner writes to stderr**, not stdout, so
   `iris chat --once "…" > out.txt` captures only the reply.
3. **`--session` defaults to `cli`**, deliberately distinct from the API's
   `default` thread so a REPL session cannot inherit/overwrite API state.
4. **`_sync_owner_chat_id` moved** from `api.py` to `engine.py` (boot logic, not
   HTTP) — an internal move with no behavior change.

## Notes and follow-ups

- **Not verified locally:** the 5 `test_memory_pipeline.py` tests still need a
  Postgres; Docker's daemon was unresponsive on this machine during the pass
  (`docker ps` hung), so they remain CI-verified. This is the same gap as P1.
- **Found while refreshing the learning vault:** `hybrid_top_k` in
  `src/iris/config.py` was defined and referenced by nothing (the last survivor
  of the v1 trigger-injection knobs the modernization pass deleted). Not fixed
  here — it is a config-surface change, not P2 scope. Flagged for the owner.
  **Resolved after P4:** `hybrid_top_k` *and* the equally dead sibling
  `mrr_top_k` were deleted from the "Recall scoring" block, which is now
  `recency_half_life_days` alone (see `p4-execution.md`).
- **`iris chat` writes to the real workspace.** The hand-run smoke test appended
  a digest line to `workspace/memory/2026-09-23.md`, as a normal turn does.
- **No commits made** — repo rule.
