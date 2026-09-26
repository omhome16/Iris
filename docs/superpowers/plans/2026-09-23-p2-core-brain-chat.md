# P2 — Core Brain + `iris chat` Implementation Plan

**Goal:** Move the engine's boot wiring out of `api.py` into `iris.harness()`, expose a public turn API (`respond` / `resume` / `stream`), and ship `iris chat` — a streaming REPL that runs the same hot path the API does, with an honest degraded mode when Postgres is absent.

**Spec:** `docs/superpowers/specs/2026-09-23-p2-core-brain-chat-design.md` (approved 2026-09-23).
**Tech stack:** unchanged — Python ≥3.12, LangGraph, asyncpg/pgvector, LiteLLM, typer, rich, pytest(+asyncio), ruff, hatchling.

## Global constraints

- `iris.api` routes and the Telegram bridge keep their current behavior; `api.py` runs the harness with `postgres="require"`.
- No new third-party dependencies.
- Degraded mode must never be silent: banner + `mode` + `degraded_reason` + an actionable recall error.
- No test may need Postgres, the network, or an API key; fakes only.
- Do not weaken existing library assertions. Do not commit unless the owner asks.
- Doctor stays offline (no Postgres check) — that is a P1 rule that still holds.

---

### Task 0: Baseline

- [ ] **Step 1:** `uv run ruff check .` → clean; `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → 234 passed. Record both; the P2 CHANGELOG entry needs the delta.

---

### Task 1: `NullIndex` + `MemoryUnavailable` (TDD)

**Files:** create `src/iris/memory/null_index.py`, `tests/test_null_index.py`; modify `src/iris/memory/index.py` (add the exception only).

**Interfaces:**
- `iris.memory.index.MemoryUnavailable(RuntimeError)` — message must contain the DSN and the fix command.
- `iris.memory.null_index.NullIndex(dsn: str, llm=None, reranker=None)` — the full index surface: `connect`, `close`, `clear_cache`, `search`, `escalate`, `nearest`, `list_chunks`, `forget_entry`, `upsert_chunks`, `delete_file_chunks`, `replace_file_chunks`, `stats`.

- [ ] **Step 1: Write failing tests** (`tests/test_null_index.py`): each recall method raises `MemoryUnavailable` and the message contains `docker compose up -d postgres`; `stats()` returns `{"chunks": 0, "degraded": True, "reason": …}`; `connect()`/`close()`/`clear_cache()` are no-ops that do not raise.
- [ ] **Step 2:** `uv run pytest tests/test_null_index.py -q` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3:** Implement. Recall methods raise; `stats` reports degraded; mutating methods (`upsert_chunks`, `delete_file_chunks`, `replace_file_chunks`, `forget_entry`) are accepted no-ops so a degraded session can still *write Markdown* (the index is derived and rebuilt later), and so nothing crashes a turn on a write path.
- [ ] **Step 4:** `uv run pytest tests/test_null_index.py -q` → PASS.

---

### Task 2: `iris.harness()` — one boot path

**Files:** create `src/iris/harness.py`, `tests/test_harness.py`; modify `src/iris/api.py` (lifespan delegates), `src/iris/__init__.py` (export `harness`, `Harness`).

**Interfaces:** exactly the spec's §3.1 (`harness()`, `Harness`, `mode`, `degraded_reason`, `respond`, `resume`, `stream`, `aclose`).

- [ ] **Step 1: Write failing tests** (`tests/test_harness.py`), all fake-driven:
  - `postgres="auto"` with a DSN nothing listens on → `mode == "degraded"`, `degraded_reason` non-empty, `isinstance(index, NullIndex)`, `h.respond("hi")` returns a non-empty string (FakeLLM/WizardLLM), and the turn appends to the daily note.
  - a tool call to `memory_search` in degraded mode returns `{"ok": false}` whose error names the fix command (drive it through `dispatch(h.runtime, "memory_search", {...})`).
  - `postgres="require"` with the same dead DSN → raises (the API's current behavior).
- [ ] **Step 2:** `uv run pytest tests/test_harness.py -q` → FAIL (no module).
- [ ] **Step 3:** Implement `harness.py` by moving the lifespan body: ledger → LLM → JEV → index/NullIndex → reindex → checkpointer (PostgresSaver | MemorySaver) → Runtime → ResearchSubagent → telegram (auto mode: connect, background retry kept on the object) → ChatGraph → scheduler → TaskScheduler. Keep the `postgres="auto"` DSN probe cheap: one `index.connect()` attempt, `except OSError/asyncpg errors` → degrade.
- [ ] **Step 4:** Refactor `api.py`'s lifespan into `async with harness(postgres="require") as brain:` storing `app.state.brain/runtime/graph`, keeping the shutdown order (cancel retry → `background.drain()` → scheduler → telegram → index → jev) in `Harness.aclose()`.
- [ ] **Step 5:** `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → still 234+ passed; `ruff` clean.

---

### Task 3: `iris chat`

**Files:** create `src/iris/cli/chat.py`, `tests/test_chat_cli.py`; modify `src/iris/cli/main.py` (register the command), `src/iris/cli/help_theme.py` if a style is missing.

**Interfaces:**
- `iris.cli.chat.run_chat(*, session: str, once: str | None, debug: bool) -> int` (importable, so tests drive it with a fake harness).
- CLI: `iris chat [--session TEXT] [--once TEXT]`.

- [ ] **Step 1: Write failing tests** (`tests/test_chat_cli.py`) with a `FakeHarness` (scripted replies, recorded sessions, scripted approval):
  - `--once "hi"` prints the reply, exits `0`, and passes `session_id`.
  - degraded harness → the banner line appears before the reply.
  - REPL: `input="hello\n/exit\n"` → one reply, exit `0`; EOF also exits `0`.
  - approval: a reply that raises `ApprovalRequired` → the prompt appears; `y` resumes `approved`, `n` resumes `cancelled`.
  - no provider key in the environment and no `.env` → exit `1` with a message naming `iris doctor`.
- [ ] **Step 2:** `uv run pytest tests/test_chat_cli.py -q` → FAIL.
- [ ] **Step 3:** Implement `run_chat`: open the harness once (`postgres="auto"`), print the banner when degraded, loop on `input()`, stream events through rich (`thinking` dim, `tool_call` as `· name(args)`, `text` inline), handle `ApprovalRequired` → prompt → `resume`, `/exit` & `/quit` & `/help`, `KeyboardInterrupt`/EOF → `0`.
- [ ] **Step 4:** Register `chat` in `cli/main.py` with a docstring that becomes its help text; update `tests/test_cli.py::test_help_lists_only_real_commands` so `chat` is allowed and `ask`/`run`/`shell` are still forbidden.
- [ ] **Step 5:** `uv run pytest tests/test_chat_cli.py tests/test_cli.py -q` → PASS; manual smoke `uv run iris chat --help`.

---

### Task 4: Docs + progress

**Files:** modify `README.md`, `CHANGELOG.md`, `docs/blueprint.md` (P2 row status); create `docs/superpowers/progress/p2-execution.md`; refresh `learning doc/`.

- [ ] **Step 1:** README: status → P2, roadmap row, CLI table gains `chat`, quickstart gains `iris chat`, a short "degraded mode" note, and the library example (`async with iris.harness() as brain:`).
- [ ] **Step 2:** CHANGELOG: `## Unreleased — P2: core brain + iris chat` with added/changed/tests (real counts), the degraded-mode contract, and the test-count delta.
- [ ] **Step 3:** `progress/p2-execution.md`: task table + verification log + DoD checklist (owner ticks the last box).
- [ ] **Step 4:** Learning vault: replace dashboard-era orientation with the library + CLI architecture (module map, boot path, turn lifecycle, where each phase's work will land).

---

### Task 5: DoD verification (hand to the owner)

- [ ] Run the spec §6 checklist; paste outputs; update the progress log; **stop before P3** until the owner ticks the DoD.

---

## Out of scope (reject during review)

- Anything from the spec §8 list: voice/image from the CLI, TUI, web surface, auto-starting Docker, a Postgres check in `doctor`, provider SDKs, memory-algorithm changes.
- Renaming the package, PyPI work, or rewriting historical specs.
