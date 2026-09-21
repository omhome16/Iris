# Changelog

Notable changes, newest first. Every entry is grounded in something measured or
verified rather than asserted — where a number appears, the method that produced
it is named.

## Unreleased — modernization pass (branch `refactor/modernize-jev`)

### Added

- **JEV (TypeSafe System One) as a typed-judgment layer** — `src/iris/jev/`,
  three integrations, each behind an adapter with a deterministic fallback:
  recall reranking (`recall.py`, composed as `relevance × decay × importance`,
  never overriding the forgetting policy), skill selection (`skills.py`), and
  instruction-injection screening for untrusted content (`guard.py`). With
  `TYPESAFE_API_KEY` unset the stack behaves exactly as before. See
  [`docs/jev.md`](docs/jev.md).
- **Capture node** (`src/iris/memory/capture.py`) — the write-path safety net.
  A deterministic prefilter keeps trivial turns free; one judgment (JEV, else
  the cheap tier) decides whether a turn holds a durable fact; the result is an
  ADD-only `(note)` line that still has to pass dreaming's Light-phase gate.
- **`/healthz` on the dashboard** — credential-free liveness so the container
  healthcheck works while the proxy routes require auth.
- **Dashboard HTTP Basic auth** (`DASHBOARD_USER` / `DASHBOARD_PASSWORD`) — the
  dashboard proxies write routes, so an open dashboard was an open
  memory-management console.
- **Owner gate on `/voice`** — the endpoint trusted a caller-supplied
  `user_id` as the graph session key.
- **Repo lint config** (`ruff`) and **CI** (`.github/workflows/ci.yml`): ruff,
  the full test suite against a real `pgvector/pgvector:pg16` service, and a
  production image build that asserts the container is non-root.
- **Test coverage for the new paths** — `tests/test_jev.py` (17),
  `tests/test_capture.py` (21), `tests/test_audit_fixes.py` (12).
  Suite: **147 → 197 passing** (5 more need Postgres and run in CI).

### Fixed

- **Groq model ids were both wrong.** `groq/groq/compound-mini` had a doubled
  provider prefix *and* named a model decommissioned 2026-09-21;
  `groq/qwen/qwen3.6-27b` named an id that no longer exists. Any Groq-keyed
  install failed over or 404'd on every call. Now `groq/openai/gpt-oss-120b` /
  `gpt-oss-20b`, verified against `console.groq.com/docs/models` (free tier).
- **Secrets could be printed.** `Settings` is a pydantic model, so
  `repr(settings)` / an f-string / a pytest failure summary dumped every API
  key and the Postgres DSN in cleartext — observed for real in a pytest
  failure summary. Every credential field is now `repr=False`, with a
  regression test.
- **`journal` never wrote.** It passed the state dict where a message list was
  expected, so the digest line and the capture both silently returned early.
  Found by the new capture tests; the shared `_turn_texts` helper now feeds both
  nodes from the same source.
- **`dreaming._consume` could crash the sleep cycle on a corrupt staging line**
  — after promotion had already happened, leaving the run half-applied.
- **A bare `"tomorrow"` scheduled at the 04:00 dream hour** instead of 09:00.
- **`read(max_tokens=…)` flattened `MEMORY.md`** into one unreadable line in the
  bootstrap budget path.
- **Morning brief was silently dead in Docker** — the core container could
  never see the `owner.json` the bridge learns from `/start`.
- **`asyncio.create_task` retry loop was unreferenced** and could be
  garbage-collected mid-flight (Telegram never reconnects after a bridge
  restart). It is now kept on `app.state` and cancelled on shutdown.
- **`.dockerignore` added** — the build context was shipping `.env`, `.venv`
  and the real `workspace/` memory into the image layer.
- **Voice-file handling** now uses `mkstemp` + `fdopen` so the descriptor is
  closed deterministically even if the upload read fails mid-stream.

### Changed

- **Container is production-shaped**: multi-stage build, non-root (uid 10001),
  `HEALTHCHECK`, workspace under `/data` as a volume. Compose mounts the host
  workspace at `/data/workspace`.
- **`_journal` and `_capture` share one turn-text helper** instead of each
  re-deriving the last human/AI message.
- **Audit-driven removals**: dead `trigger_*` config, an unused Telegram
  `user` variable, unused imports, and duplicated `dream_now` logic in `/sleep`.
- **Docs corrected against the code** — README claims, `docs/superpowers/specs`
  (a dead `use_llm_triggers` documented as live), and the OpenRouter/Groq model
  table.

### Known limits (stated, not hidden)

- The reranker has **no before/after eval numbers yet**: Docker was unavailable
  in this pass, so the eval lab could not run against a real index. The eval lab
  gained a `no_rerank` ablation mode and a JEV force-off switch, so the
  measurement is one command away — treat the rerank's benefit as a hypothesis.
- Model names were verified against provider docs, not executed against a live
  key (no keys in this environment).
- `workspace/skills/*` is untracked and **not** gitignored — a blanket
  `git add` would publish real skills. Left as a decision for the owner.
