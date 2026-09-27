# P3 — Telegram Client Seam Implementation Plan

**Goal:** Give the library one brain-client contract, make the Telegram bridge a consumer of it, make delivery idempotent, and fix the broken two-phase `/forget` the bridge depends on.

**Spec:** `docs/superpowers/specs/2026-09-23-p3-telegram-client-design.md`
**Tech stack:** unchanged. The new library modules must be **stdlib + httpx only**.

## Global constraints

- Bridge behavior is unchanged: command surface, owner gate, progressive edits, typing, photo/voice, MCP tools.
- `tests/test_telegram_bridge.py` stays green **unmodified** — it is the refactor's regression net.
- No new dependencies anywhere (library or bridge image).
- `iris/channels/brain.py` and `iris/channels/updates.py` must not import the engine (LangGraph/asyncpg/agent/memory).
- No test may need Postgres, the network, or a key. Do not weaken assertions. No commits unless asked.

---

### Task 0: Baseline

- [ ] `uv run ruff check .` clean; `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → 255 passed. Record for the CHANGELOG delta.

### Task 1: Fix the two-phase `/forget` (TDD)

**Files:** `src/iris_ai/memory/index.py` (hit field + both SELECTs + both constructions), `src/iris_ai/api.py` (harden `/forget`), `tests/test_forget_route.py` (new).

- [ ] **Step 1:** Write the failing test: stub a runtime whose index returns a `MemoryHit` **with** `chunk_index=3`, call the `/forget` endpoint function directly, assert the candidate payload carries `chunk_index == 3`; and a hit with the default (`-1`) is skipped instead of crashing.
- [ ] **Step 2:** `uv run pytest tests/test_forget_route.py -q` → FAIL (`AttributeError` / missing field).
- [ ] **Step 3:** Add `chunk_index: int = -1` to `MemoryHit`; select `chunk_index` in `_search_rows` and `_escalate_rows`; populate it in both `MemoryHit(...)` calls; skip `h.chunk_index < 0` in the route.
- [ ] **Step 4:** Green, and `tests/test_memory_units.py` + `tests/test_jev.py` still pass (scoring untouched).

### Task 2: `iris/channels/brain.py` — one client contract (TDD)

**Files:** `src/iris_ai/channels/brain.py` (new), `tests/test_brain_client.py` (new), `tests/test_brain_client_imports.py` (new).

- [ ] **Step 1:** `tests/test_brain_client.py`: `parse_sse_line` handles `text`/`reply`/`tool_call`/`approval`/`thinking`/`error`/`thinking_done`, ignores non-`data:` lines and malformed JSON without raising. `HttpBrainClient` with `httpx.MockTransport`: `stream()` yields the ordered event kinds for a scripted SSE body; `respond()` and `resume()` POST the right JSON to `/chat` and `/chat/resume`; `json_get("/mind")` returns the decoded dict; the bearer header is attached when a token is configured.
- [ ] **Step 2:** FAIL (no module).
- [ ] **Step 3:** Implement `BrainEvent`, `parse_sse_line`, the `BrainClient` protocol and `HttpBrainClient` (injected client or owned client, `core_url` normalised like the bridge did).
- [ ] **Step 4:** `tests/test_brain_client_imports.py`: run `python -c "import iris_ai.channels.brain"` in a subprocess and assert none of `langgraph`, `asyncpg`, `iris_ai.agent`, `iris_ai.memory` appear in `sys.modules`. This is what keeps the bridge image small.
- [ ] **Step 5:** Green + ruff.

### Task 3: `iris/channels/updates.py` — idempotent delivery (TDD)

**Files:** `src/iris_ai/channels/updates.py` (new), `tests/test_telegram_updates.py` (new).

- [ ] **Step 1:** Tests: `normalize_update` classifies text / command / photo (with caption) / voice / unsupported (returns None) / `edited_message`; `UpdateLedger` starts empty; `is_duplicate` is False for a new id and True after `mark_processed`; `next_offset()` is `watermark + 1`; state survives a reload (the restart-replay case) and the seen-set stays bounded (`capacity`).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement with a JSON file + atomic replace (`tmp` + `os.replace`), tolerant of a corrupt/missing file.
- [ ] **Step 4:** Green + ruff.

### Task 4: Wire the bridge onto the library

**Files:** `mcp_servers/telegram/server.py`, `mcp_servers/telegram/Dockerfile` (install the package `--no-deps`), `docker-compose.yml` if the build context changes.

- [ ] **Step 1:** Replace `_core_headers()`/inline httpx calls in `CommandDispatcher` with `brain = HttpBrainClient(IRIS_CORE_URL, token=IRIS_API_TOKEN)`; keep every reply string and the pending-forget dict exactly as they are.
- [ ] **Step 2:** Replace the SSE parsing + payload building in `_stream_chat_turn` with `async for event in brain.stream(text, session_id=str(chat_id), image=image)`; keep the throttle, the first-send/edit/flush logic, the typing loop and the approval→cancel→`resume` behaviour.
- [ ] **Step 3:** `_poll_loop`: `ledger = UpdateLedger(DATA_DIR / "updates.json")`, `ledger.load()`, `offset = ledger.next_offset()`, skip `ledger.is_duplicate(u["update_id"])`, and `ledger.mark_processed(...)` + `ledger.save()` after each update is handled. `normalize_update` decides the branch.
- [ ] **Step 4:** Verify: `uv run pytest tests/test_telegram_bridge.py -q` (unchanged, green) and `uv run python -c "import ast,pathlib; ast.parse(pathlib.Path('mcp_servers/telegram/server.py').read_text(encoding='utf-8'))"` (syntax), since the bridge is not importable from the library's venv.
- [ ] **Step 5:** Bridge Dockerfile: build from the repo root context, `pip install --no-deps .` after the existing requirements (documented as unverified here — no Docker daemon on this machine).

### Task 5: Docs + progress + DoD

- [ ] README: the bridge-as-client note, the idempotency property, and the P3 status row.
- [ ] CHANGELOG: P3 section (added/changed/fixed/tests, real counts) including the `/forget` fix.
- [ ] `docs/blueprint.md`: P3 marked shipped; note the deferred in-process client.
- [ ] `docs/superpowers/progress/p3-execution.md`: tasks, verification log, DoD checklist.
- [ ] Run the DoD commands; hand the checklist to the owner; **stop before P4**.

---

## Out of scope (reject during review)

- `LocalBrainClient` / in-process bridge mode (needs the API view builders extracted first — spec §2).
- Webhook mode, multi-bot fleets, MediaGroups, inline keyboards.
- Any change to the graph, memory algorithms, JEV, persona, or the owner gate.
- Rewriting historical specs.
