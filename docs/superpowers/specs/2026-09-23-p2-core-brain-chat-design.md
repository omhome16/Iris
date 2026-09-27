# P2 — Core brain: the library turn pipeline + `iris chat`

**Date:** 2026-09-23
**Status:** Approved — §1–§3 confirmed by the owner on 2026-09-23 (see "Decisions")
**Phase:** P2 of 8 (P1 skeleton → **P2 core brain** → P3 Telegram → P4 skills → P5 multi-agent → P6 cron → P7 computer-use → P8 ship)
**Gate in:** P1 DoD verified (`docs/superpowers/progress/p1-execution.md`)
**Gate out:** no P3 work until the owner verifies the P2 DoD

---

## Decisions (owner-confirmed 2026-09-23)

| Question | Decision |
|---|---|
| What happens when no Postgres is reachable for `iris chat`? | **Best UX:** chat starts anyway in a clearly-labelled degraded mode (in-memory checkpointer, no vector recall), with a loud banner and an honest tool error. Nothing pretends to be a full session. |
| How is the turn pipeline exposed? | **`iris_ai.harness()` async context manager** — the boot wiring moves out of `api.py` into the library, and `api.py` is refactored onto it, so there is exactly one wiring path. |
| What about the local `learning doc/` vault? | **Refresh it** for the library + CLI shape (it currently documents the deleted dashboard). |

---

## 1. Context

P1 deleted the web dashboard and made the repo library-first, but the engine is
still only *bootable* through `iris_ai.api`'s FastAPI lifespan. That wiring —
WorkspaceFiles, cost ledger, LLM client, JEV, pgvector index, reindex, LangGraph
Postgres checkpointer, Runtime, ChatGraph, scheduler, task scheduler — is the
product's mind, and today it lives inside a web framework module. The Telegram
bridge is the only client that can talk to it, and it talks over HTTP.

P2 makes the library the mind and the CLI a real client of it.

## 2. Goals and non-goals

### Goals

1. **One wiring path.** Extract the boot sequence into `src/iris_ai/harness.py`; `api.py` delegates to it (behavior unchanged).
2. **A public turn API.** `Harness.respond()`, `Harness.resume()`, `Harness.stream()` — the same methods the API and (P3) the bridge call.
3. **`iris chat`.** A streaming REPL on that path: `--once` for a single turn, `--session` for continuity, an interactive HITL approval prompt, clean Ctrl+C/EOF exits.
4. **Degraded mode, honestly labelled.** No Postgres → in-memory LangGraph checkpointer, no vector recall, a banner at start, and a recall tool that *says* it is unavailable (never a silent "I don't remember").
5. **Tests with no network and no database.** Fakes only.
6. **Docs that match.** README status/quickstart, CHANGELOG, `progress/p2-execution.md`, and the learning vault refreshed.

### Non-goals

- No Telegram-library integration (P3), no skills registry (P4), no
  multi-agent orchestration (P5), no cron surface (P6), no computer-use (P7), no
  packaging/release work (P8).
- No change to `iris_ai.api` route behavior, the Telegram bridge, memory
  algorithms, JEV integrations, or the persona.
- No new provider SDKs: the provider abstraction is the existing LiteLLM-backed
  `LLMClient` plus `LLM_PROVIDER`/key selection in `config.py`.
- No TUI, no Textual, no web surface of any kind.

## 3. Interfaces

### 3.1 `iris_ai.harness()` — the library boot path

```python
# src/iris_ai/harness.py
PostgresMode = Literal["require", "auto"]

@asynccontextmanager
async def harness(
    *,
    workspace_dir: Path | None = None,
    postgres: PostgresMode = "auto",
) -> AsyncIterator[Harness]: ...

@dataclass(slots=True)
class Harness:
    files: WorkspaceFiles
    llm: LLMClient
    ledger: CostLedger
    jev: JevClient
    index: MemoryIndex | NullIndex
    runtime: Runtime
    graph: ChatGraph
    mode: Literal["full", "degraded"]
    degraded_reason: str | None

    async def respond(self, text: str, *, session_id: str = "default",
                      image: str | None = None) -> str: ...
    async def resume(self, session_id: str, *, decision: str) -> str: ...
    def stream(self, text: str, *, session_id: str = "default",
               image: str | None = None) -> AsyncIterator[tuple[str, object]]: ...
```

- `respond`/`resume`/`stream` delegate to `ChatGraph`, so every client shares
  one hot path (and one 120 s turn budget, applied by the caller as today).
- `postgres="require"` is the **API** mode: a missing database still fails at
  boot exactly as it does today. `postgres="auto"` is the **CLI** mode: the
  harness degrades instead of dying.
- `Harness.aclose()` drains background passes, closes the index/JEV/telegram
  handles; the context manager calls it.

### 3.2 Degraded mode

| Concern | Full | Degraded |
|---|---|---|
| Checkpointer | `AsyncPostgresSaver` | `MemorySaver` |
| Index | `MemoryIndex` (pgvector) | `NullIndex` |
| Recall (`memory_search`, `escalate`) | hybrid search | raises `MemoryUnavailable`, whose message names the DSN and the fix (`docker compose up -d postgres`) |
| Markdown writes (`memory/YYYY-MM-DD.md`, captures, `note`/`remember`) | yes | **yes** — evidence still accrues on disk and is indexed the next time Postgres is up |
| Reindex at boot | yes | skipped |
| JEV | on when keyed | unchanged (on when keyed) |
| Reflection / capture | unchanged | unchanged |
| `mode` / `degraded_reason` | `"full"` / `None` | `"degraded"` / human-readable cause |

`NullIndex` implements the exact surface the rest of the code calls
(`search`, `escalate`, `nearest`, `list_chunks`, `stats`, `upsert_chunks`,
`delete_file_chunks`, `replace_file_chunks`, `forget_entry`, `close`,
`clear_cache`). The `_tools` node already converts a raised exception into an
`{"ok": false, "error": …}` tool result, so the agent is told the truth in
words rather than hallucinating around an empty result set.

### 3.3 CLI surface

```
iris chat [--session TEXT] [--once TEXT]
iris chat --help
```

| Behavior | Detail |
|---|---|
| Prompt | `you> ` on stdin; reply streams to stdout |
| Streaming | reasoning shown dimmed as `· thinking`, tool calls as `· memory_search(query=…)`, reply tokens printed inline, final newline |
| Banner | degraded mode prints a warning line naming the cause and the fix command before the first prompt |
| HITL | a paused turn prints the approval payload and asks `approve? [y/N]`; `y` resumes with `approved`, anything else with `cancelled` |
| In-REPL commands | `/exit` (also `/quit`), `/help` |
| `--once "text"` | runs one turn and exits (scriptable; no prompt echo) |
| `--session` | thread id, default `cli` — the same session id the API uses for its default |
| Ctrl+C / EOF | exits `0` without a traceback; `--debug` re-raises |
| No provider key | exits `1` with an actionable message pointing at `iris doctor` and `.env.example` |
| `--version` / `--debug` | inherited from the root callback as today |

## 4. Freezing the public surface

`src/iris_ai/__init__.py` gains exactly two public names beyond `__version__`:
`harness` and `Harness`. Everything else stays internal until the phase that
needs it (P3 will add the Telegram client seam). The P1 spec's rule still holds:
do not freeze agent internals as public API.

## 5. Tests

| File | Covers |
|---|---|
| `tests/test_harness.py` | degraded wiring end to end (FakeLLM + MemorySaver + NullIndex): `mode`, `degraded_reason`, a turn replies, recall tool error names the fix, the daily note still gets written; `postgres="require"` raises when the index cannot connect |
| `tests/test_chat_cli.py` | REPL driven through `CliRunner(input=…)` against a fake harness: `--once` prints one reply and exits 0; banner in degraded mode; approval prompt resumes with the right decision; `/exit` and EOF exit 0; missing provider key exits 1 with the actionable message |
| `tests/test_cli.py` | updated: `chat` is now a real command (the old test asserted it must **not** exist — that assertion's job is now to forbid `ask`/`run`/`shell` only) |
| `tests/test_null_index.py` | `NullIndex` raises `MemoryUnavailable` from every recall method, returns a degraded `stats()`, and its message contains the DSN + fix command |

No test may require Postgres, a network call, or an API key.

## 6. Definition of Done (owner verification)

```text
[ ] uv run iris --help            → version, doctor, chat (and nothing fake)
[ ] uv run iris chat --help       → session/once flags documented
[ ] uv run iris chat --once "hi"  → one reply (full mode with Postgres + a key)
[ ] uv run iris chat              → REPL: streams, banner when degraded, /exit works
[ ] python -c "import asyncio, iris; ..." → async with iris_ai.harness() as h: await h.respond("hi")
[ ] uv run ruff check .           → clean
[ ] uv run pytest tests -q        → green (count recorded in CHANGELOG)
[ ] README quickstart includes iris chat and matches reality
```

## 7. Risks

| Risk | Mitigation |
|---|---|
| Extracting the boot path changes API behavior | `api.py` keeps `postgres="require"` and delegates in a thin lifespan; all existing API/route tests stay green unchanged |
| Degraded mode masquerades as a full session | Loud banner, `mode`/`degraded_reason` on the object, recall error names the fix; a test pins each of the three |
| REPL tests hang on `input()` | Drive stdin through `CliRunner(input=…)`; no bare `input()` in tests |
| Ctrl+C leaves background work running | `Harness.aclose()` awaits `background.drain()` exactly as the API shutdown does |
| Boot-time Postgres check becomes a startup tax | One connect attempt; degradation is a warning, and the DSN is reported, not retried in a loop |
| Session continuity surprises (a CLI session shares threads with the API default) | Default session id is `cli`, distinct from the API default `default`; documented in `--help` |

## 8. Out of scope (reject during review)

- `iris chat --voice`, image input from the CLI, a TUI, or a web view.
- Auto-starting Docker/Postgres, or a Postgres check in `iris doctor`
  (doctor stays offline by the P1 rule).
- Provider SDK additions; any change to `src/iris_ai/memory/**` algorithms.
- Rewriting historical specs under `docs/superpowers/specs/`.

## 9. Approval

- [x] §1 context and decisions — owner, 2026-09-23
- [x] §2 goals / non-goals — owner, 2026-09-23
- [x] §3 interfaces (harness + degraded mode + CLI) — owner, 2026-09-23
- [ ] P2 implemented and the owner has run the DoD checklist
