# Changelog

Notable changes, newest first. Every entry is grounded in something measured or
verified rather than asserted — where a number appears, the method that produced
it is named.

## Unreleased — console + latency pass

### Added

- **A judgment panel, and the data behind it.** `src/iris/turnlog.py` records
  per-turn what the judgment layer decided and how long each stage took;
  `src/iris/agent/chat.py` writes both into `config/traces.jsonl`
  (`events`, `counts`, `stages_ms`). The console renders it as a waterfall plus
  per-memory probabilities and guard verdicts. "Not checked" is now recorded as
  explicitly as "checked and clean".
- **`GET /jev`** (authenticated) — judgment-layer health: enabled or not, why
  not, request/failure counters, last latency and last error. `/health` exposes
  the same block as `judgment`, alongside `background.pending`.
- **`src/iris/background.py`** — tracked fire-and-forget tasks with `drain()`,
  because a bare `asyncio.create_task` can be garbage-collected mid-flight.
- **The console rebuilt as one canvas** (`dashboard/templates/index.html`,
  `static/style.css`, `static/app.js`): a dawn-sky single-page board with nine
  panels — conversation, judgment, all four memory tiers, dream diary,
  forgetting curve, skills, schedule, spend, turn history — plus a night theme.
  Screenshots in `docs/screenshots/`, design notes in `docs/console.md`.
- **`/mind` returns `agents` (AGENTS.md), `daily` and `today`** — the persona
  file and the episodic tier were missing from the snapshot, so the console could
  show what Iris believes but never what she was told today.

### Changed

- **The reflection pass is off the reply path.** It only appends to a telemetry
  file, yet it was awaited, costing every retrieval-backed turn an extra
  cheap-tier completion (~2–6 s) before the graph returned. Now a tracked
  background task, with `IRIS_REFLECTION_BACKGROUND=0` to force inline. The
  trace records which mode ran.
- **`append_daily` writes one block in a single call** so the journal digest and
  the capture note line cannot interleave their shared timestamp.
- **JEV client construction is lock-guarded**, since judgements can now overlap.
- **Capture records its rejection reason** (`prefilter declined`, `already in
  context`, `daily cap reached`, …) so a quiet turn is distinguishable from a
  broken one.

### Fixed

- **Dawn-theme text failed WCAG AA.** The faint tier measured 2.8–3.4:1 against
  the sky and panels; it is now 4.9+:1, and 22 text styles pass in both themes.
- **The retention chart deleted its own accessible name** — the redraw counted
  `childNodes` (whitespace included) and removed the `<title>`/`<desc>` that
  `aria-labelledby` points at.
- **`<dt>`/`<dd>` outside a `<dl>`** in the spend panel (axe `dlitem`), and
  scrollable regions that were not keyboard-focusable.
- **A name collision** where the new turn log shadowed the module logger inside
  `ChatGraph.respond`, breaking the recursion-limit path with an
  `AttributeError`.

## Unreleased — modernization pass (branch `refactor/modernize-jev`)

### Added

- **JEV (TypeSafe System One) as a typed-judgment layer** — `src/iris/jev/`,
  three integrations, each behind an adapter with a deterministic fallback:
  recall reranking (`recall.py`, composed as `relevance × decay × importance`,
  never overriding the forgetting policy), skill selection (`skills.py`), and
  instruction-injection screening for untrusted content (`guard.py`). With
  `TYPESAFE_API_KEY` unset the stack behaves exactly as before. See
  [`docs/jev.md`](docs/jev.md).
- **Capture shows up in the turn trace** — `config/traces.jsonl` records what
  the capture node wrote (`capture`, empty when the prefilter or the judgment
  declined), rendered as `💭 [importance] fact` in the dashboard's traces panel.
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
