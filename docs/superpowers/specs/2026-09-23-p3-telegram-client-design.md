# P3 — Telegram as a first-class client of the library

**Date:** 2026-09-23
**Status:** Approved — owner said "proceed" on 2026-09-23 (the P2 DoD gate)
**Phase:** P3 of 8 (P2 core brain → **P3 Telegram** → P4 skills → …)
**Gate in:** P2 DoD verified (`docs/superpowers/progress/p2-execution.md`)
**Gate out:** no P4 work until the owner verifies this phase's DoD

---

## 1. Context

`iris chat` and the HTTP API already share one brain (`iris_ai.harness()`). The
Telegram bridge does not: `mcp_servers/telegram/server.py` (638 lines) hand-rolls
its own brain client — it builds `/chat/stream` requests, parses SSE lines by
hand, re-derives the event kinds, keeps its own copy of every command's HTTP
call, and holds the Telegram update offset **in memory only** (`offset = 0` at
every boot, `_poll_loop`).

Two consequences show up in the code:

1. **The client contract is duplicated.** The SSE event shapes (`text`, `reply`,
   `tool_call`, `approval`, `error`) are defined in `iris/agent/chat.py`, re-sent
   by `iris/api.py`, and re-parsed by the bridge. A change on either side is
   invisible to the other.
2. **Delivery is not idempotent.** Telegram redelivers unconfirmed updates; with
   the offset in memory, a restart (or a crash mid-turn) replays updates — and
   each replay runs a *new turn* through the graph. The blueprint lists
   "idempotent delivery" as a P3 deliverable, and this is the concrete failure
   it names.

Also found while reading for this phase (live bug, P3 fixes it):

3. **`/forget` cannot complete.** The route reads `h.chunk_index` from a
   `MemoryHit`, but `MemoryHit` has never carried a `chunk_index` — neither
   recall lane selects the column. So `/forget` raises `AttributeError` (a 500),
   and the bridge's two-phase forget is broken end to end. The Telegram flow is
   the primary consumer of this route, which is why the fix belongs here.

## 2. Goals and non-goals

### Goals

1. **One client contract, owned by the library.** `iris/channels/brain.py`
   defines the event shape and the turn/resume/command surface once.
2. **The bridge consumes it.** The streaming turn and the command dispatcher in
   `mcp_servers/telegram/server.py` are replaced by calls into the library
   client; the bridge keeps only Telegram mechanics (poll, send, edit, typing,
   photo/voice download) and the owner gate.
3. **Idempotent delivery.** A persisted update watermark plus a bounded seen-set,
   so a restarted or crashed bridge does not re-run turns.
4. **`/forget` works**, with a regression test on the candidate payload.
5. **No behavior regression:** command surface, owner gate, progressive message
   editing, approval handling, photo/voice paths, MCP tools.

### Non-goals

- **In-process brain mode (`LocalBrainClient`) is deliberately deferred.** It
  needs the read-only view builders (`/mind`, `/rot`, `/retention`, `/skills`,
  `/tasks`) extracted out of `api.py` into the library; without that extraction
  it would either duplicate those views or drag the whole engine into the bridge
  image. Recorded as a candidate for P4/P8 — the seam is designed so it is a
  new class, not a rewrite.
- No multi-bot fleets, no webhook mode (long-poll only, as today), no skills or
  cron surface, no change to the persona, graph, memory algorithms or JEV.
- No new third-party dependencies. The bridge keeps `mcp`, `httpx`, `uvicorn`.

## 3. Interfaces

### 3.1 `iris/channels/brain.py` — the client contract

**Dependency rule:** this module may import **stdlib + httpx only**. It must not
pull LangGraph, asyncpg, or anything from `iris_ai.agent` / `iris_ai.memory`, so the
bridge image can install the package with `pip install --no-deps .` and stay
small. (Enforced by a test that imports the module in a subprocess without the
engine present — see §5.)

```python
@dataclass(slots=True)
class BrainEvent:
    kind: Literal["thinking", "text", "reply", "tool_call", "approval", "error", "thinking_done"]
    delta: str = ""
    text: str = ""
    call: dict | None = None
    payload: dict | None = None


def parse_sse_line(line: str) -> BrainEvent | None:
    """`data: {...}` → BrainEvent, anything else → None (never raises)."""


class BrainClient(Protocol):  # structural; both transports satisfy it
    async def respond(self, text: str, *, session_id: str, image: str | None = None) -> str: ...
    def stream(self, text: str, *, session_id: str, image: str | None = None) -> AsyncIterator[BrainEvent]: ...
    async def resume(self, session_id: str, *, decision: str) -> str: ...
    async def json_get(self, path: str) -> dict: ...
    async def json_post(self, path: str, payload: dict) -> dict: ...


class HttpBrainClient:
    """iris-core over HTTP: the deployed bridge's client."""
    def __init__(self, core_url: str, *, token: str | None = None, timeout: float = 300.0,
                 client: httpx.AsyncClient | None = None) -> None: ...
```

- `stream()` yields `BrainEvent`s parsed by `parse_sse_line`, so no client
  hand-parses SSE again.
- `json_get`/`json_post` carry the bearer token when configured — the contract
  the bridge's `_core_headers()` implemented inline.
- An injected `httpx.AsyncClient` makes the whole client testable with
  `httpx.MockTransport` (no network).

### 3.2 `iris/channels/updates.py` — idempotent delivery

```python
@dataclass(slots=True)
class InboundUpdate:
    update_id: int
    chat_id: int
    kind: Literal["text", "command", "photo", "voice", "other"]
    text: str = ""
    caption: str = ""
    raw: dict = field(default_factory=dict)


def normalize_update(update: dict) -> InboundUpdate | None:
    """Telegram update dict → InboundUpdate; None for updates we ignore."""


class UpdateLedger:
    """Remembers what was already processed, across restarts.

    - `watermark` — the highest update_id that was fully handled; Telegram
      redelivers everything above the offset we pass back, so a monotonic
      watermark suppresses replay after a clean restart.
    - `seen` — a bounded set of recent update_ids for the crash-mid-batch case,
      where the watermark has not advanced yet.
    Persisted as one small JSON file (default `mcp_servers/telegram/data/updates.json`).
    """

    def __init__(self, path: Path, *, capacity: int = 500) -> None: ...
    def load(self) -> None: ...
    def is_duplicate(self, update_id: int) -> bool: ...
    def mark_processed(self, update_id: int) -> None: ...   # advances watermark or records a gap
    def next_offset(self) -> int: ...                       # watermark + 1
    def save(self) -> None: ...                             # atomic replace
```

### 3.3 Bridge (`mcp_servers/telegram/server.py`)

- `CommandDispatcher` → a thin wrapper over `HttpBrainClient`: same commands, same
  formatted replies, but every HTTP call goes through the client (and the
  pending-forget state stays where it belongs — the bridge's per-chat session).
- `_stream_chat_turn` → iterates `client.stream(...)` yielding `BrainEvent`s;
  the Telegram edit/throttle logic stays (it is transport, not brain).
- `_poll_loop` → `UpdateLedger` for the offset and dedupe; `normalize_update`
  decides text/command/photo/voice.
- The bridge gains a module-level `brain = HttpBrainClient(IRIS_CORE_URL, token=IRIS_API_TOKEN)`.

### 3.4 `/forget` fix (`iris/api.py`, `iris/memory/index.py`)

- `MemoryHit` gains `chunk_index: int = -1`, selected and populated by both
  recall lanes.
- `/forget` skips candidates without a chunk index instead of crashing, and
  `/forget/confirm` keeps its existing path/chunk contract.

## 4. Tests

| File | Covers |
|---|---|
| `tests/test_brain_client.py` | `parse_sse_line` (all kinds, malformed input, non-data lines); `HttpBrainClient` against `httpx.MockTransport`: a streamed turn yields the right event kinds in order, `respond`/`resume` post the right payloads + bearer header, `json_get`/`json_post` round-trip |
| `tests/test_brain_client_imports.py` | the dependency rule: importing `iris_ai.channels.brain` in a fresh subprocess must not import `langgraph`, `asyncpg`, `iris_ai.agent` or `iris_ai.memory` |
| `tests/test_telegram_updates.py` | `normalize_update` for text/command/photo/voice/unsupported/edited; `UpdateLedger` watermark + seen-set + persistence + a restart replay scenario (the exact bug) |
| `tests/test_forget_route.py` | `/forget` returns a usable `chunk_index` for a `MemoryHit`; a hit without one is skipped rather than crashing |

The existing `tests/test_telegram_bridge.py` (dispatcher behavior) must still
pass — it is the regression net for the bridge refactor.

## 5. Definition of Done (owner verification)

```text
[ ] uv run ruff check .                 → clean
[ ] uv run pytest tests -q              → green (count recorded in CHANGELOG)
[ ] bridge imports the library client   → rg "iris_ai.channels.brain" mcp_servers/telegram/server.py
[ ] idempotency is real                 → test proves a replayed update_id is dropped
[ ] /forget returns candidates with chunk_index (regression test)
[ ] README + CHANGELOG + blueprint + progress/p3-execution.md updated
```

## 6. Risks

| Risk | Mitigation |
|---|---|
| The bridge refactor changes Telegram behavior | `tests/test_telegram_bridge.py` is kept green unchanged; the dispatcher's replies and the owner gate are not touched, only the HTTP calls underneath |
| The library module drags the engine into the bridge image | dependency rule above + a subprocess import test; the bridge Dockerfile installs with `--no-deps .` |
| A too-eager watermark drops a real message | the ledger only advances *after* a turn completes, and keeps a bounded seen-set for the mid-batch crash case; `next_offset()` is `watermark + 1`, never `max(seen) + 1` |
| `/forget` fix changes recall scoring | it only adds a field to the hit record; selection/scoring are untouched |

## 7. Approval

- [x] Owner: "proceed" (2026-09-23) — P2 DoD gate opened, P3 direction confirmed
- [ ] P3 implemented and the owner has run the DoD checklist
