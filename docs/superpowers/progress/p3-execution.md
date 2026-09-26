# P3 Execution Progress

**Plan:** `docs/superpowers/plans/2026-09-23-p3-telegram-client.md`
**Spec:** `docs/superpowers/specs/2026-09-23-p3-telegram-client-design.md`
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-23
**Rules:** No commits unless the owner explicitly asks. No P4 work until the owner
verifies this phase's DoD.

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline (ruff + suite before touching anything) | done | `ruff` clean; 255 passed with the DB suite excluded (P2 exit state) |
| 1 | `MemoryHit.chunk_index` + `/forget` route (TDD) | done | `tests/test_forget_route.py` (3) written failing first; field added, both search queries select it, `chunk_index < 0` skipped instead of crashing |
| 2 | `iris_ai.channels.brain` — one brain-client contract | done | `src/iris_ai/channels/brain.py`; `tests/test_brain_client.py` (18) + `tests/test_brain_client_imports.py` (2, subprocess import guard) |
| 3 | `iris_ai.channels.updates` — normalization + idempotency ledger | done | `src/iris_ai/channels/updates.py`; `tests/test_telegram_updates.py` (15); atomic `os.replace`, bounded seen-set, tolerant of a missing/corrupt file |
| 4 | Bridge rewired onto the library | done | `mcp_servers/telegram/server.py`: `CommandDispatcher`, `_stream_chat_turn`, the poll loop and the owner gate now use `HttpBrainClient` / `normalize_update` / `UpdateLedger`; new `_handle_update()` split out of the loop; unchanged `tests/test_telegram_bridge.py` still green |
| 4b | Bridge image installs the library | done | `mcp_servers/telegram/Dockerfile` rewritten (repo-root context, `pip install --no-deps .`); `docker-compose.yml` build context updated; wheel verified to contain `iris/channels/brain.py`, `iris/channels/updates.py` |
| 5 | Docs: README, CHANGELOG, blueprint, progress | done | README status → P3 + bridge-as-client section + history entry; CHANGELOG P3 section (incl. the `/forget` fix and the live JEV evidence); blueprint P3 marked shipped |
| 6 | DoD verification (hand to owner) | **awaiting owner ticks** | evidence below |

## Verification log

All commands from the repo root, 2026-09-23.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | `294 passed, 1 warning` (255 → 294: +18 brain client, +2 import guard, +15 updates, +3 forget route, +1 poll-loop replay) |
| `uv run pytest tests -q` | `294 passed` + the 5 Postgres tests erroring loudly without a database (by design; they run in CI) |
| `uv run pytest tests --collect-only -q` | `299 tests collected` (294 runnable here + the 5 pgvector tests) |
| `uv run pytest tests/test_telegram_bridge.py tests/test_telegram_updates.py tests/test_brain_client.py tests/test_brain_client_imports.py tests/test_forget_route.py -q` | `54 passed` — the whole bridge surface, no bot token, no network, no DB |
| `tests/test_telegram_bridge.py::test_poll_loop_does_not_replay_a_handled_update` | boots `_poll_loop` twice against one ledger file with a fake `getUpdates` that redelivers until the offset advances → `handled == [42]`, and the reloaded ledger reports `next_offset() == 43` |
| `uv run python -c "import ast,pathlib; ast.parse(pathlib.Path('mcp_servers/telegram/server.py').read_text(encoding='utf-8'))"` | parses (the bridge is importable in the venv too, via `tests/test_telegram_bridge.py`) |
| `uv run iris --help` | commands are exactly `chat`, `version`, `doctor` — unchanged |
| `uv run iris doctor` | `TYPESAFE_API_KEY: set` (name only; the doctor never prints values) |
| Live JEV call (`JevClient.ask`, real `SystemOne`) | model `jev-1.13.0`, noul `0.98`, score `1.98`, 367 input tokens, 1057 ms — the JEV integrations run live, not on fallbacks |
| Live JEV rerank (`JevReranker.relevance`, the recall integration) | `enabled: True`, `max_candidates: 20`, `blend: 0.15`; scores `[0.97, 0.02, 0.01]` for (real answer / same topic / unrelated) — the blended rerank has a live signal to work with |
| Live JEV call with a deliberately corrupted key | `ask()` returned `None`, logged a 401 and the caller fell back — the degradable design works in both directions |
| `uv build --wheel` + archive inspection | the wheel carries `iris/channels/brain.py` and `iris/channels/updates.py`, so the bridge image build (repo root, `--no-deps`) has what it imports |
| Import guard, no-deps reasoning | `import iris_ai.channels.brain` pulls in only `httpx`, `mcp` and their deps — no LangGraph, asyncpg, litellm, pgvector, fastapi, typer |

## DoD checklist (owner must tick)

- [x] The bridge imports the library for turns and commands (`HttpBrainClient`) — no second HTTP implementation
- [x] `mcp_servers/telegram/server.py` keeps transport only (poll, send, edit, typing, download, owner gate)
- [x] A replayed `update_id` cannot run a second turn (ledger + tests)
- [x] `/forget` confirm carries a real `chunk_index`; a hit without one is reported, not crashed on
- [x] Unsupported message types still get the same reply as before the refactor
- [x] `uv run ruff check .` clean
- [x] `uv run pytest -q` green apart from the 5 pgvector tests (CI runs all)
- [ ] **Owner confirmation** — the gate for P4

## Deviations from the plan (with reasons)

1. **`normalize_update()` never returns `None` for a message it cannot answer.**
   The plan had the sticker/document case return `None`. That silently dropped
   the bridge's existing reply ("I can only read text, voice, or photos for
   now."), i.e. a user-visible regression. It now returns `kind="other"` and the
   caller sends that reply; `None` is reserved for updates with no chat/message
   at all (status updates, joins), which were silent before too.
2. **`InboundUpdate` carries `photo` / `voice`.** The plan's shape kept only
   `raw`. Digging the media dict back out of `raw` in the bridge would re-derive
   what normalization had already located — two places to keep in sync — so the
   normalized fields are part of the dataclass instead.
3. **The bridge's `/voice` and photo-download calls still use `_core_headers()`.**
   They are multipart/file-download transports, not the JSON/SSE brain contract;
   moving them would grow the client interface without removing a second
   implementation of anything. Revisit only if a third caller appears.
4. **Two pre-existing test fixtures needed `chunk_index`** (`tests/test_jev.py`,
   `tests/test_recall_cache.py`). They hand-build rows for the search SQL, which
   selects `chunk_index` (a `NOT NULL` column); the fixtures were incomplete
   relative to the query, and the new field surfaced it as 9 failures.

## Notes and follow-ups

- **Not verified locally:** the 5 `test_memory_pipeline.py` tests still need
  Postgres. Docker's daemon was unresponsive on this machine during the pass
  (`docker version` → `rc=124`), so they remain CI-verified — the same gap P1 and
  P2 recorded. For the same reason the **bridge image itself was not built**:
  the Dockerfile change is reasoned + wheel-verified, not `docker build`-verified.
- **`TYPESAFE_API_KEY` is now set in `.env`** (`.env` is gitignored — verified
  with `git check-ignore`), so JEV paths run live from here on. Worth re-running
  the recall ablation (`scripts/eval_lab.py`) with JEV enabled once Postgres is
  reachable: the existing `reports/eval_lab.md` numbers were produced on the
  deterministic fallback path.
- **Deferred:** an in-process `LibBrainClient` (same-host deployments skip the
  HTTP hop) and Telegram webhook mode. Both are additive and neither changes the
  client contract.
- **Still open from P2:** `hybrid_top_k` in `src/iris_ai/config.py` is read by
  nobody. A config-surface change, so it stays out of phase scope. **Resolved
  after P4** — `hybrid_top_k` and `mrr_top_k` deleted; see `p4-execution.md`.
- **No commits made** — repo rule.
