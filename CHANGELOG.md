# Changelog

Notable changes, newest first. Every entry is grounded in something measured or
verified rather than asserted — where a number appears, the method that produced
it is named.

## Unreleased — the publish pass: a name that exists, twelve providers, and an artifact that installs

**Phase:** after the hardening pass, aimed at the first public release. Suite:
**824 passed**, ruff clean, `uv build` produces `iris_personal_ai-0.2.0` with the
builtin skill inside it.

### Changed

- **Renamed to something publishable.** `iris` on PyPI is another project's, and
the `iris` *import* is SciTools Iris — so the distribution is now
`iris-personal-ai`, the import package is `iris_ai`, and the console command
stays `iris`. Verified free: `iris-ai`, `iris-mind` and `iris-personal-ai` all
404 on PyPI, `iris` returns 200.
- **Providers became a registry.** Twelve providers (including OpenCode Zen and
Go, verified endpoints `/zen/v1` and `/zen/go/v1` sharing `OPENCODE_API_KEY`),
plus a bring-your-own `openai-compatible` provider for vLLM, LM Studio or any
gateway. `Settings`, the failover chain and `iris doctor` all read one table.
A provider with a key but no model id is **skipped rather than attempted** — a
guessed model id is a 404 on every turn, which is exactly how two shipped Groq
defaults had already failed. `iris doctor` gained `model provider` and
`strong model` checks.

### Fixed

- **The shipped skill did not survive `pip install`.** The wheel packaged only
`src/iris_ai`, so `iris skills list` on an installed copy had no builtins while
the README called that skill "the format's proof". Hatch now force-includes
`skills/` as `iris_ai/builtin_skills` (and the Mermaid assets), `builtin_root()`
falls back to the packaged copy, and CI runs `iris skills list` from `/tmp` —
where the checkout's `skills/` is not on the path — as a gate.

### Added

- **A release workflow** (`.github/workflows/release.yml`): tag → build → install
and run the artifact → TestPyPI → PyPI, using OIDC trusted publishing so no
long-lived token exists in the repo.
- **LICENSE, authors, urls, classifiers, keywords**, and a version that matches
the changelog (0.1.0 → 0.2.0).
- **`docs/vault-review.md`** — Iris against a 112-note AI-engineering vault:
what already matches, what was adopted, what was deliberately rejected and why,
and the accepted backlog.

---

## Unreleased — the hardening pass: the day ceiling made real, JEV on the last decision, and a CLI worth looking at

**Phase:** after P8 ([`docs/blueprint.md`](docs/blueprint.md)), driven by three
questions: is the shipped code carrying anything dead, is JEV doing all the work
it can, and can somebody else pick this up. Suite: **806 passed**, ruff clean,
with the pgvector service running locally for once.

### Fixed

- **The per-day token ceiling never fired.** `Budget.note_usage()` had no caller
  outside the tests, so the cross-session bound the P8 audit asked for read zero
  forever: `refusal()` compared today's counters against the ceiling while nothing
  ever incremented them. `ChatGraph._bank_turn()` now banks a finished turn's
  spend from a `finally` inside the turn log (the only moment the per-tier usage
  exists) — including the recursion bail-out path, because a turn that spent
  tokens must be counted even when it did not finish. Two graph-level tests pin
  it: one turn spends, the *next* turn's tool calls are refused by the day
  ceiling.
- **The `CACHED` counter had no source.** The client already read cached prompt
  tokens for the cost ledger, but `turnlog` dropped them, so the split-by-kind
  budget had a permanently zero bucket. Cached tokens now travel with the rest of
  the usage and land in their own bucket.
- **`iris guards` printed a `↑`**, which is not in cp1252 — on a Windows console
  that is not a missing glyph but a `UnicodeEncodeError` traceback instead of a
  table. The readout is now ASCII-bordered and cp1252-clean, and a source-level
test asserts no CLI literal can regress it.

### Added

- **`iris guards` + `GET /guards`** — the pre-tool chain and today's budget, read
  from settings and `config/budget.json`, so the ceilings are inspectable with no
  engine running (the route adds live circuit state). A limit nobody can read is a
  limit nobody can trust.
- **The start screen.** `iris` with no arguments draws a generated iris-at-dawn
  mark (`src/iris_ai/cli/art.py`) instead of dropping straight into help. It is
  computed from a polar pattern rather than pasted, so it fits the terminal, is
  symmetric by construction, and is tested by property instead of a golden file —
  and it never draws into a pipe, under `NO_COLOR`, or with `IRIS_NO_BANNER=1`.
  `iris chat` gets the same mark (skippable with `--no-banner`).
- **JEV now decides the reflection pass** (`src/iris_ai/memory/reflection.py`). The
  hallucination triage was the last LLM-driven *decision* on the turn path: one
  cheap-tier completion per retrieval-backed turn, whose output was an opaque
  list. It is now one batched request that returns a **support probability per
  claim sentence**, with the cheap model kept as the fallback. Same decision, no
  completion billed, and the probabilities are recorded in the turn trace
  (`reflection_claims`) so a flag is a number with a threshold.
- **A latency budget for the reply-path judgment.** The recall rerank blocks the
  agent's next call, so `JEV_RERANK_TIMEOUT_SECONDS` (default 2.5 s) is enforced
  inside the client timeout: past it the deterministic shortlist wins and the turn
  keeps moving. Without one, a slow judgment layer cost the full 12 s timeout on
  every recall — which is how "JEV is optional" quietly stops being true.
- **`docs/jev.md` §3.0 — the audit of every model call.** A table of each call
  site and its verdict: seven JEV integrations, and the calls that stay with the
  LLM because they need *generation*. Two candidates that were considered and
  **rejected for now** are recorded there with the reason (a quality change needs
  evidence before it ships), rather than quietly skipped.
- **`CONTRIBUTING.md`, `docs/architecture.md`, `docs/extending.md`** — setup and
  verify commands, the turn lifecycle, the ten invariants a plausible edit would
  break, where each kind of state lives, and a recipe for every extension point
  (tool, skill, channel, role, guard, eval metric, setting) with the test that
  catches you.
- **Every setting is documented.** The audit found 50 of 121 `Settings` fields
  absent from `.env.example` — including `TRACE_CONTENT`, `SKILL_GUARD_GATE` and
  the whole multi-agent and cron bound sets — so they were undiscoverable. All 50
  are documented, and a new test enforces both directions: no key without a
  setting, no setting without a key. (`docs/jev.md`'s "three judgments" line had
also gone stale; the list is now complete.)

### Removed

- **`skill_from_skill_md`** — a leftover alias for `parse_skill_md` with no callers
  after the P4 manifest refactor.
- **`Grants.revoke`** — unused, and unused code in a security boundary is a
  liability rather than an API.
- **Three copies of the auth header.** `iris_ai.security` now owns
  `bearer()`/`auth_headers(token=None)`; `HttpBrainClient` and the Telegram bridge
  call it instead of rebuilding the string. The dead copy declared the header and
  documented clients that had stopped using it.

### Tests

`uv run pytest tests -q` → **806 passed** with the pgvector service up (774 at
P8 exit: +32 across the JEV reflection path and its trace, the day-budget wiring,
the latency budget, `iris guards`, the art properties and the cp1252 rule).
`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → 801 passed with
no database at all. `uv run ruff check .` clean, and `docker build -t iris:local .`
produces an image whose `id` is `uid=10001(iris)` — the non-root claim, verified
rather than asserted.

---

## Unreleased — P8: guards that refuse before the spend, and approvals bound to what was shown

**Phase:** P8 of 8, the last one ([`docs/blueprint.md`](docs/blueprint.md)). The
conformance audit found four harness gaps (G1–G4) and one eval gap (G6); P8 closes
all five and adds the release hygiene that lets someone else install the result.
Plan: [`plans/2026-09-25-p8-ship.md`](docs/superpowers/plans/2026-09-25-p8-ship.md);
log: [`progress/p8-execution.md`](docs/superpowers/progress/p8-execution.md).

### Added

- **`src/iris_ai/guards.py` — a pre-tool guard chain that runs *before* dispatch.**
  Fixed order: `budget → circuit → spiral/dedup → context → record`. Each guard
  can only ever **refuse**; none can re-open what another closed. Every verdict is
  deterministic and model-free — no JEV call, no LLM call, no network — because a
  guard that needs a model to decide whether to spend money can itself run away.
- **Spiral detection with the vault's thresholds (audit G1).** The same tool with
  near-identical arguments ≥3×, argument **Jaccard > 0.72**, or more than the
  declared calls in a turn (the normal is 1–3) is refused before the call is made.
  Arguments are flattened leaf-by-leaf, lowercased and stripped, so case and
  whitespace do not disguise a loop and legitimately varying arguments do not trip
  it. A refused call **never reaches `dispatch`**, asserted by a test rather than
  by reading the call site.
- **A cascade breaker (audit G2).** Two consecutive failures of the same tool open
  that tool's circuit for the rest of the run (`unavailable — do not retry`); three
  distinct failing tools escalate the turn. A success resets the streak, so a tool
  that failed once and then worked is not punished for a flake. The circuit fires
  **before** the spiral guard, so a retry storm is attributed to the tool that is
  failing rather than counted as repetition.
- **`src/iris_ai/budget.py` — scoped ceilings with counters split by kind (audit
  G3).** Input / output / cached / embedding / tool-schema tokens are counted
  separately, because they fail differently; a per-turn ceiling joins the existing
  per-turn work and a **per-day** ceiling survives a new turn *and a restart*
  (`config/budget.json`). `0` means no ceiling everywhere. The policy carries a
  version, recorded with the numbers it produced, so a measurement can name its
  policy instead of "the budget".
- **`src/iris_ai/approval.py` — approval integrity (audit G4).** The interrupt
  envelope now carries the **effective digest of the arguments after edits**, so
  an edited resume cannot pass as the original; one `tool_call_id` grants **once
  per thread**; resuming a thread with nothing waiting is refused; and a
  side-effecting action whose envelope carries no digest **fails closed** whether
  the owner said yes or no. The graph wiring stays thin enough to review, because
  the invariants are unit-testable without a graph.
- **`src/iris_ai/eval/` — eval gates with statistics (audit G6).** Wilson intervals
  for rates, a seeded bootstrap for means, a **paired** interval for "candidate vs
  baseline", a measured noise floor, `samples_needed(Δ, σ)` that *derives* the
  ~63-samples-per-arm figure for Δ=0.02 at σ=0.04, Cohen's κ for judge–human
  agreement, and a pre-registered `DecisionRule` / `decide` that reports
  `inconclusive` rather than a pass when the rule cannot pass. Stdlib only
  (`statistics.NormalDist`), so it is tested without a database or a model.
- **`scripts/eval_lab.py` reports intervals, not points.** `render_report` is a
  pure function of its measurements, so the report's shape is tested without
  running the lab; the report carries CIs and the noise floor.
- **A CI `package` job** — `uv build`, install the wheel in a clean venv, then run
  the console entry (`iris version`, `iris --help`). A build that only exists in
  `pyproject.toml` is a claim, not a deliverable.
- **`docs/support.md`** — the support matrix (Python, OS, providers, the pgvector
  requirement), what CI verifies and where, and how a release is cut.
- **`tests/test_packaging.py`** — one version source, a declared console entry, the
  support doc, and the rule that **every key in `.env.example` names a real
  setting**: a sample config that documents a knob nobody reads is a lie.

### Fixed

- **A resume with no approval waiting was handed to the graph.** Resuming a
  finished thread fell through to `Command(resume=…)`, which is not an approval —
  it is a replay or a bug. It is now refused with a reason and recorded as
  `resume_refused` in the turn trace.
- **Budgets were per-turn only, and a single total.** A per-turn ceiling bounds a
  loop; it does not bound a day, so a runaway that spent a little every turn was
  invisible, and one number could not say *what* moved. Now both scopes are
  enforced and the counters are split.
- **Nothing refused a *loop*, only recorded one.** Every guard Iris had was
  observational. The refusal now happens pre-dispatch, where it costs nothing.
- **Two test doubles reached the network.** `LLMClient` is a real client, and the
  journal's reflection pass calls `complete` on any owner turn that retrieved
  memory — so the doubles in `tests/test_guard_wiring.py` and
  `tests/test_subagents.py`, which overrode only `complete_with_tools`, inherited a
  live provider call. That is what the suite's one lingering warning was pointing
  at (`coroutine 'VertexLLM.async_completion' was never awaited`: a LiteLLM
  coroutine the closed loop dropped). Both now answer offline, and `tests/fakes.py`
  records the rule for future doubles.

### Tests

`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → **769 passed**
(was 684 at P7 exit: +85 across the guard chain, budgets, approval, the wiring of
both, eval statistics and packaging); `uv run ruff check .` → clean. **Zero
warnings**, which is a change: the warning that carried over from P7 was a test
double inheriting a real provider call, and fixing it makes the suite's
"deterministic, no API calls" claim true rather than aspirational. Docker remains
out of bounds by owner instruction, so `docker build` and the five
Postgres-backed tests stay CI-verified.

The audit's gaps, and where each one now lives: G1/G2 → `guards.py`, G3 →
`budget.py`, G4 → `approval.py`, G5 → P6's trace content policy, G6 →
`eval/stats.py`.

---

## Unreleased — P7: a declared tool surface, and screen control that can say no

**Phase:** P7 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). The conformance
audit found the tool surface was governed by hand-maintained allowlists and that
adding screen control would put a capability nobody had fenced one tool-call
away. P7 fixes both before adding the capability. Plan:
[`plans/2026-09-24-p7-computer-use.md`](docs/superpowers/plans/2026-09-24-p7-computer-use.md);
log: [`progress/p7-execution.md`](docs/superpowers/progress/p7-execution.md).

### Added

- **`src/iris_ai/toolpolicy.py` — tools declare a class, and the class derives
  policy.** Every tool declares one of `read` / `filesystem` / `memory_write` /
  `network` / `credentialed` / `delivery` / `control`; the class yields a default
  (`allow` / `ask` / `deny`). Resolution is **most-specific-wins** (per-tool →
  class → class default) with **`deny` always winning**, so a class-wide deny
  cannot be re-opened one tool at a time. Overrides parse from `.env`
  (`TOOL_POLICY_OVERRIDES="send_message=deny,control=allow"`); a malformed
  entry raises at boot, while an unknown *name* is reported by `iris tools`
  instead of stopping the process.
- **A visible-surface budget and `find_tools`.** Each tool is declared `core`
  (never deferred) or `extended` (deferrable). Past `tool_surface_budget`
  (default 20) extended tools are deferred, last-declared first; `find_tools`
  returns the schema of a deferred tool so it is still reachable. Deferral is
  **presentation, not permission** — a deferred tool is still callable, core
  tools are never hidden at any budget, and a connected channel's own tools are
  promoted past the budget.
- **`src/iris_ai/computer/` — a screen action, fenced.** A closed vocabulary
  (`screenshot` / `navigate` / `click` / `type`), a provider protocol with a
  `NullProvider` default and a lazily-imported `PlaywrightProvider`, a permission
  model (suffix-matched host/title allowlists on label boundaries,
  unconditional confirmation for the destructive subset, a per-session grant
  with an action budget) and an append-only action audit log.
- **One `computer` tool**, not five: `computer(action=…)` with `control` class and
  `ask` by default. Registered **only when `computer_enabled`** is set — off
  means the capability does not exist in the surface at all.
- **`iris tools` / `iris tools actions`, `GET /tools`, `GET /actions`** — the
  policy readout (class, resolved policy, source, deferral) and the action log,
  so a decision nobody can inspect cannot pretend to have happened.

### Fixed

- **Typed text never enters a log.** A `type` action records a length and a
  digest, never the text — an action log containing what was typed is a
  keylogger. The approval prompt likewise carries `text_chars`, not the text.
- **A missing computer-use driver is a refusal, not an exception.** An absent
  Playwright install reports a single stable `unavailable` reason instead of
  raising `ImportError` in the middle of a turn; an unavailable driver refuses
  *before* the owner is asked to approve anything.
- **Two hand-maintained allowlists are gone.** `NON_OWNER_BLOCKED` and the
  research role's `READ_ONLY_TOOLS` were allowlists with no default: a new tool
  stayed classified only if someone remembered to edit them. A coverage test now
  pins the declaration table to `TOOL_NAMES` in both directions.

### Tests

- `tests/test_tool_policy.py` (43), `tests/test_computer_provider.py`,
  `tests/test_computer_permissions.py`, `tests/test_computer_audit.py`,
  `tests/test_computer_tool.py`, `tests/test_tools_cli.py` — **684 passed**,
  ruff clean. Docker remains out of bounds by owner instruction.

---

## Unreleased — P6: scheduled jobs, and a trace policy that keeps secrets out

**Phase:** P6 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). Time became a
first-class trigger — recurrence and failure behaviour are now explicit data in
one store rather than a second scheduler — and the trace stopped writing
whatever a model put in a tool argument. Plan:
[`plans/2026-09-24-p6-cron.md`](docs/superpowers/plans/2026-09-24-p6-cron.md);
log: [`progress/p6-execution.md`](docs/superpowers/progress/p6-execution.md).

### Added

- **Two new job kinds** (`src/iris_ai/tasks.py`) — `every <interval>` (anchored to
  the last run) and calendar recurrence (`daily at 09:00`, `mon,wed,fri at
  18:30`, in your timezone), beside the existing `once`. Recurrence is parsed by
  the same grammar the agent's `schedule_task` tool already used, so there is one
  grammar rather than two, and a pre-P6 `tasks.json` loads unchanged.
- **A declared misfire policy** — `missed_decision()` is the single decision
  point for "the window passed while nobody was watching": a recurring job that
  missed several windows fires **once** (coalesced, never once per window), a
  stale one-off is counted as `missed` and recorded *before* it is dropped, and a
  job that keeps failing disables itself past a threshold instead of retrying
  forever. Every job now carries `runs`, `misses`, `failures`, `last_outcome`,
  `last_run` and `last_error`.
- **`iris cron list | add | rm`** (`src/iris_ai/cli/cron.py`, `iris cron`) — `list`
  is read-only, needs no engine and no broker, and prints schedule, next run,
  outcome counters and last result. `add` writes the same store the agent writes
  (so there is no second configuration surface) and `--at` / `--every` / `--once`
  force an explicit form; `rm` takes a prefix and **refuses an ambiguous one**
  rather than guessing.
- **`POST /cron/reload`** (`src/iris_ai/api.py`) — lets a live engine pick up CLI
  changes without a restart. The CLI tells you when this is needed instead of
  implying the job is already live.
- **`src/iris_ai/redact.py`** — pattern-based redaction for bearer tokens, `key=value`
  credentials, provider key shapes and URL userinfo. It is the backstop, not the
  whole policy, and its docstring says so rather than overselling completeness.
- **Trace content policy** (`IRIS_TRACE_CONTENT`) — `metadata` (default),
  `redacted`, `sampled`, `full`. Applied in `TraceLogger.record`, so every trace
  path is covered by construction rather than by each caller remembering.

### Fixed

- **The turn trace wrote raw tool arguments to disk.** `_trace_turn` called
  `json.dumps(tc.get("args"))`, so any secret the model passed as an argument was
  persisted in `traces.jsonl`. Arguments are now recorded as an `args_hash` in
  metadata mode — which also makes loop/spiral detection possible later — and
  redacted in every other mode. Credentials never reach the trace file in any
  mode.
- **A stale one-off task vanished silently.** `register_all()` dropped past tasks
  with only a log line, so "why didn't my reminder fire?" had no answer. It is now
  counted and its outcome recorded.
- **Nothing in `src/` redacted anything**: `grep -riE "redact|scrub" src/iris_ai`
  returned zero hits before this phase.

### Tests

596 passed, 1 warning (was 515 at P5 exit: +35 job kinds and misfire policy,
+23 `iris cron`, +22 redaction, +1 trace policy); ruff clean. Three assertions
were updated because the phase intends to change them: the two command-set
checks (six commands now) and `test_chat_turn_records_trace`, which asserted the
raw user message was in the trace — under a metadata default it now asserts the
hash and length, and a new test pins that `trace_content = redacted` brings the
text back.

## Unreleased — P5: roles, handoffs and a delegation policy

**Phase:** P5 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). The research
subagent became a **role**, a **critic** joined it, every exchange between them
became a typed value with provenance, and code — not a model — owns the caps.
Multi-agent stays opt-in per turn: the lead delegates by calling a tool, and it
always ends up holding the pen.

### Added

- **Roles as declared data** (`src/iris_ai/agents/roles.py`) — a frozen `Role` with
  `tier`, `search_lane`, `tools`, `max_tool_rounds` and `max_output_chars`. The
  researcher is cheap-tier and read-only on the escalation lane; the critic is
  the *opposite* tier on the default lane. A role's allowlist **intersects** with
  what the session grants (`narrow`), an undeclared tool name is a startup error,
  and no role may call `deep_dive` — a subagent that can spawn subagents is a
  recursion with no bottom.
- **The handoff protocol** (`src/iris_ai/agents/handoff.py`) — `Claim` + `Source` +
  `Spend` + `Handoff`. `unsourced` is *derived* from the sources, so it cannot
  disagree with them, and `render_findings` flattens a finding onto one line so a
  retrieved page cannot forge a header or open a code fence while impersonating
  the prompt.
- **`RoleRunner`** (`src/iris_ai/agents/runner.py`) — the shipped research subgraph,
  generalized over a role. A run records every source it consulted, so **a report
  produced without consulting anything is marked unsourced** — the
  anti-fabrication rule made mechanical rather than aspirational.
- **`Orchestrator`** (`src/iris_ai/agents/orchestrator.py`) — code-owned policy:
  2 calls per turn, a 20 s deadline, fan-out capped at 3 and gated by the effort
  judgment, one revision gated by the sufficiency judgment, and refusals that
  carry a stable reason (`budget_exhausted`, `deadline_exceeded`, …).
  Deterministic merge: refusals are dropped (a refusal is not a finding),
  unsourced claims keep their marker, and the cap is derived from the per-role
  cap times the call cap so the two cannot disagree.
- **Two JEV judgments** (`src/iris_ai/jev/agents.py`) — *effort* (fan out?) and
  *sufficiency* (is this draft grounded?). Both fail open; code keeps every
  threshold. The sufficiency judgment runs first and alone, so a draft it is
  confident about costs one request instead of a whole critic invocation.
- **`verify_answer` tool**, and `deep_dive` now routed through the orchestrator:
  it returns findings **with provenance** and tells the model how many were
  unsourced.
- **`iris agents roles | show <name> | handoffs`** and **`GET /agents`** — the
  blueprint's "which agent decided what", reading the same declarations the
  runner enforces and the same trace store `/traces` already serves.
- **Per-turn token accounting** (`turnlog.add_usage`, fed from `LLMClient._record`)
  — the ~15x cost of multi-agent is now visible per turn instead of being found in
  the ledger later.

### Changed

- **The turn's allowance is scoped per turn**, keyed on the turn log's own turn
  id. The first draft of the orchestrator held one budget on the instance, so the
  call cap would have been spent once, on the first turn of the process, and every
  later turn would have been refused — caught while writing the JEV tests.
- `tests/test_agent_graph.py` and `tests/test_cli.py` tool/command-set assertions
  include the new names (`verify_answer`, `agents`).

### Fixed

- **Telemetry could break a reply.** The first `trace_summary()` emitted a `kind`
  key, which collides with `turnlog.record(kind, **fields)`'s positional
  parameter — a `TypeError` raised while *binding* the call, before turnlog's own
  guard could catch it, and it broke the shipped worker. The key is now
  `handoff_kind`, and the call site is guarded too: turnlog's "recording never
  raises" contract does not cover argument binding.
- **A trace entry could carry a whole report.** `TurnLog.add` truncates top-level
  strings but passes nested structures through untouched, so handoff claims in the
  trace would have put the full text in every line. `trace_summary()` is scalars
  only; the full payload is `Handoff.as_dict()`.

### Tests

`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → **515 passed**
(P4: 410) with `uv run ruff check .` clean. The 105 new tests are exactly the P5
suites: +18 roles, +15 handoffs, +33 orchestrator, +18 JEV judgments, +12
`iris agents`/route, +9 token accounting. The five `test_memory_pipeline.py`
tests still need Postgres and remain CI-verified.

**Live JEV evidence, not a mock** (`TYPESAFE_API_KEY` set, `jev-latest`, 4
requests, 0 failures): effort `0.960` → fan out vs `0.040` → don't; sufficiency
`0.920` → grounded vs `0.020` on a draft asserting two facts with no findings.
Both gates are one number (0.60) with a wide margin on each side.

## Unreleased — cleanup: two dead recall knobs deleted

Owner-selected cleanup while Docker was unavailable, not a phase deliverable.

### Removed

- **`hybrid_top_k` and `mrr_top_k`** from the "Recall scoring" block in
  `src/iris_ai/config.py` (now `recency_half_life_days` alone, renamed "Recall
  decay"). Neither was read by any code path: shortlist size and MMR diversity
  are explicit per-call arguments (`MemoryIndex.search` / `escalate`), and every
  caller passes its own (`agent/tools.py:93,95,464`, `api.py:488`). A single
  global default could not have served the agent tool, the escalation lane and
  the research subagent at once, so they were deleted rather than wired in.
- **Two no-op test patches.** `tests/test_subagents.py` set
  `settings.mrr_top_k = 3` in its two research-routing tests — vestigial from a
  v1 regex-driven research path, and inert regardless, because both tests stub
  the index. The patch, its import and the unused fixture argument are gone.

No behaviour change, and no config breakage: `SettingsConfigDict(extra="ignore")`
means a stale `HYBRID_TOP_K`/`MRR_TOP_K` in a local `.env` is still ignored
rather than rejected. Verified with `uv run ruff check .` (clean) and
`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → **410 passed**.

## Unreleased — P4: skill registry, manifests, execution boundary

**Phase:** P4 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). Skills became a
registry: discovered from four sources, validated against the tool surface,
selected under a policy, and — if they ship code — executed through exactly one
gated path. The learning loop (dreaming, `skill_write`, reinforce/revise) is
untouched, and pre-P4 skills load unchanged.

### Added

- **A manifest on `Skill`** — `version`, `source`, `enabled`, `allowed_tools`,
  `timeout_seconds`, `license`, `compatibility`, `root`, `scripts`,
  `references`, `metadata`. Every field is defaulted, so the sidecars already on
  disk (`svg-pro`, `create_svg_art`) still load; only the *name* convention
  produces a warning, because the agent invents prose names mid-conversation.
- **The open Agent Skills encoding** (`src/iris_ai/skills/manifest.py`) — a
  directory with `SKILL.md` (YAML frontmatter: `name`, `description`, `license`,
  `compatibility`, `metadata`, `allowed-tools`) plus optional `scripts/`,
  `references/` and `assets/`, per <https://agentskills.io/specification>. Iris
  keeps `triggers`/`success_score` and carries them in `metadata.iris-triggers`,
  so JEV selection and the trigger matcher work for both encodings.
- **`SkillRegistry`** (`src/iris_ai/skills/registry.py`) — four sources in
  precedence order (workspace learned → workspace directories → `SKILLS_EXTRA_DIRS`
  → entry-point packages → repo builtins). A name clash is a `RegistryConflict`
  naming winner, loser and path; malformed skills are excluded **and reported**;
  ordering is deterministic; nothing is cached (dreaming rewrites skills while
  the process runs). Reads are registry-wide, writes stay in `SkillLibrary` and
  refuse shipped/hand-authored skills rather than creating shadow copies.
- **Skill policy** (`src/iris_ai/skills/policy.py`) — an active skill's
  `allowed-tools` narrows the turn at the single choke point (`dispatch`, plus
  the schemas offered to the model). Empty means unrestricted; an unknown tool
  name is a validation error; the policy intersects with the session rules and
  can never widen them. Refusals are recorded in the turn trace.
- **The script boundary** (`src/iris_ai/skills/runner.py`, `guard.py`) and the
  `skill_run` tool — resolve inside the skill's own `scripts/`, a deterministic
  AST pre-screen (code, not prose), a **binding** JEV judgment gate, owner
  approval carrying the findings and arguments, then a subprocess with a
  constructed environment (no keys, no `.env`), a timeout and capped output.
- **`iris skills list | show <name> | validate`** — read-only; `validate` exits
  1 on error-level issues so it works as a gate. The command registry is now
  exactly `{chat, doctor, skills, version}` (asserted).
- **A shipped builtin skill**, `skills/web-page-to-notes/` — the format's proof:
  standard frontmatter plus a stdlib-only, offline `scripts/extract.py`, with a
  test that runs it for real.
- **`pyyaml` declared** in `pyproject.toml`: it was already in the lock via
  litellm, but iris code now imports it.

### Changed

- **`runtime.skills` is the registry** (typed `SkillRegistry`), so context
  injection now draws from `selectable()` — a disabled or invalid skill is never
  named in the prompt, because naming one is what activates its policy. The
  assembler returns which skills it named (`assemble_turn`), and the graph keeps
  them in `active_skills` state for the policy to read.
- Tool schemas are filtered by the policy, and `get_tools` asserts every
  registered tool appears in the declared `TOOL_NAMES` set — the same set a
  manifest is validated against, so the two cannot drift.

### Fixed

- **A skill script now runs in the API process.** Found by the full suite: the
  first implementation used `asyncio.create_subprocess_exec`, which is
  unimplemented on the Windows Selector loop that `iris_ai.api` selects for psycopg.
  Execution goes through a worker thread around `subprocess.run` instead, and a
  test pins it.
- **The pre-screen no longer reads prose as evidence.** The first version scanned
  raw source, and the shipped `extract.py` was flagged "uses the network"
  because its docstring *says* it is offline. That false finding travelled into
  the judgment's state and dragged a safe script to 0.56 — below the gate. It now
  parses the AST and judges code only.

### Tests

`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → **410 passed**
(P3: 294) with `uv run ruff check .` clean: +23 manifest, +20 registry, +15
policy, +31 runner/guard, +13 `skill_run` tool, +11 CLI, +1 builtin end-to-end.
The five `test_memory_pipeline.py` tests still need Postgres and remain
CI-verified.

**Live JEV evidence, not a mock** (`TYPESAFE_API_KEY` set): the shipped script was
judged **0.72 allowed**, a credential-exfiltrating variant **0.01 refused**. That
data point also moved the gate: the config default is now `0.60`, because 0.70
left a genuinely safe script one judgment-quantum from a refusal.

## Unreleased — P3: Telegram as a first-class client

**Phase:** P3 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). The Telegram
bridge stopped being a second implementation of the brain and became a client of
the library. Agent graph, memory algorithms, JEV integrations and the HTTP API
were **not** changed — and the bridge's user-visible behavior is unchanged apart
from the bug fix below.

### Added

- **`iris_ai.channels.brain`** — the one definition of the brain-client contract:
  `BrainClient` (protocol), `HttpBrainClient`, `BrainEvent` and
  `parse_sse_line()`. The CLI and the bridge now share it, so a change to the
  HTTP/SSE shape is a one-file change instead of two. It imports nothing heavy:
  `tests/test_brain_client_imports.py` runs it in a subprocess and asserts
  `langgraph`, `asyncpg`, `iris_ai.agent` and `iris_ai.memory` stay out of
  `sys.modules` — that is what keeps the bridge image small.
- **`iris_ai.channels.updates`** — `normalize_update()` (text / command / photo /
  voice / other, plus `edited_message`, returning `None` only for updates with
  nothing to answer) and `UpdateLedger`, a bounded JSON ledger persisted beside
  `owner.json` (atomic `os.replace`, tolerant of a missing or corrupt file).
- **`tests/test_brain_client.py`, `tests/test_brain_client_imports.py`,
  `tests/test_telegram_updates.py`, `tests/test_forget_route.py`** — all
  fake-driven: no bot token, no network, no database.
- The bridge image now installs the library it imports
  (`pip install --no-deps .`, built from the repo root — `docker-compose.yml`
  updated accordingly).

### Changed

- **The bridge is a library client.** `CommandDispatcher`, `_stream_chat_turn`
  and the poll loop now call `HttpBrainClient` instead of hand-rolling requests,
  SSE parsing and payloads. What stays in `mcp_servers/telegram/server.py` is
  transport only: long-polling, sending, progressive edits, typing, file
  downloads, the owner gate.
- **Updates are idempotent across restarts.** The long-poll offset and the set
  of handled update ids live in a ledger on disk, so a restart resumes where it
  stopped instead of replaying an update into a second turn (which also meant a
  second memory write).

### Fixed

- **`/forget` never worked: `MemoryHit` had no `chunk_index`.** The confirm step
  edits one specific chunk, but `MemoryHit` never carried its index — it came
  from `row["chunk_index"]` at the call site, i.e. an `AttributeError` on every
  real hit, so the two-phase forget flow could only ever report failure. The
  field now exists, both search queries select it, and a hit without one is
  reported as unactionable instead of crashing.

### Tests

`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` → **294 passed**
(P2: 255: +18 brain client, +2 import guard, +15 updates, +3 forget route, +1
bridge poll-loop replay test) with `uv run ruff check .` clean. The five
`test_memory_pipeline.py` tests still need Postgres and remain CI-verified (no
Docker daemon was reachable on this machine). The bridge's own suite is green:
54 tests over `test_telegram_bridge.py`, `test_telegram_updates.py`,
`test_brain_client*.py` and `test_forget_route.py` — including one that boots the
poll loop twice against one ledger file and asserts the update is handled once.

**JEV verified live this phase:** with `TYPESAFE_API_KEY` set, a real
`SystemOne` call returned model `jev-1.13.0` (noul 0.98 relevant / score 1.98,
367 input tokens, 1057 ms). Before that, the same call through a corrupted key
degraded exactly as designed — `ask()` returned `None`, logged a 401 and let the
caller fall back.

## Unreleased — P2: core brain + `iris chat`

**Phase:** P2 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). The engine's boot
path moved out of the web framework and into the library; `iris chat` is a real
client of it. Agent graph, memory algorithms, JEV integrations and the Telegram
bridge were **not** changed.

### Added

- **`iris_ai.harness()` / `iris.Harness`** — the public turn API, re-exported
  lazily by `iris/__init__.py` so `import iris_ai` stays cheap: `respond()`,
  `resume()`, `stream()` plus the wired engine (`files`, `index`, `runtime`,
  `graph`, `jev`). The HTTP API, the CLI and (P3) the bridge now share this one
  hot path.
- **`iris chat`** — a streaming REPL on that path: live text, tool-call lines,
  the human-in-the-loop approval prompt (approve/cancel → `resume`), `/exit`,
  `/help`, and `--once "…"` for a scriptable single turn. `--session` selects
  the thread (default `cli`, deliberately distinct from the API's `default`).
  `iris --help` now lists `chat`, and `tests/test_cli.py` asserts the command
  registry is exactly `{chat, doctor, version}` — a stub still cannot sneak in.
- **Degraded mode** — `postgres="auto"` (the CLI) keeps the conversation alive
  when no database is reachable: in-memory LangGraph checkpointer, no vector
  recall, and a banner naming the cause and the fix. `postgres="require"` (the
  API) still fails at boot, unchanged.
- **`iris/memory/null_index.py` + `MemoryUnavailable`** — the degraded index
  stand-in. Recall raises with the fix command in the message (never an empty
  result set, which would read as "I don't remember"); the derived-index write
  methods are accepted no-ops, so captures and `remember`/`note` lines still
  land in the daily note and are indexed once Postgres is back.
- **`tests/test_harness.py`** (7) and **`tests/test_chat_cli.py`** (11),
  plus **`tests/test_null_index.py`** (3) — all fake-driven, no database, no
  network, no keys.

### Changed

- **`api.py`'s lifespan is now a client** — it opens
  `iris_ai.harness(postgres="require")` and hands the routes
  `app.state.runtime` / `app.state.graph`. The previous wiring (ledger → LLM →
  JEV → index → reindex → checkpointer → Runtime → graph → scheduler → tasks →
  Telegram) moved verbatim into `src/iris_ai/engine.py`; shutdown ordering
  (cancel retry, `background.drain()`, scheduler, telegram, index, JEV) moved
  into `Harness.aclose()`. **No route behavior changed** — the full suite,
  including the API-auth introspection tests, passes unmodified.
- **`_sync_owner_chat_id`** moved from `api.py` to `iris/engine.py` (it is boot
  logic, not HTTP).
- **Why the module is `iris_ai.engine`, not `iris_ai.harness`:** a submodule and a
  package attribute cannot share a name — importing `iris_ai.harness` would set the
  attribute to the *module* and silently clobber the `harness()` callable the
  public API promises. The implementation lives in `iris_ai.engine`; the public
  name is `iris_ai.harness()`.
- **`tests/test_cli.py`** — `test_help_lists_only_real_commands` no longer
  forbids `chat` (it is real now) and asserts the registry is exact instead.
- **`iris/__init__.py`** freezes exactly `__version__`, `harness`, `Harness` as
  the public surface. Nothing else from the engine is public yet.

### Tests

- **260 collected**, **255 passing locally** with
  `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` (234 → 255:
  +7 harness, +11 chat CLI, +3 null index).
- The 5 pgvector pipeline tests are unchanged and still run in CI (all 260).
- The CLI's real path was also exercised by hand against the live provider:
  `uv run iris chat --once "…"` → a reply, exit `0`, degraded banner on stderr,
  and the turn appeared in `config/traces.jsonl` (`session_id: "cli"`) plus a
  digest line in `memory/<date>.md` — i.e. a degraded session still leaves the
  evidence a full one would.

## Unreleased — P1: library-first skeleton + CLI shell

**Phase:** P1 of 8 ([`docs/blueprint.md`](docs/blueprint.md)). The web dashboard
is gone; the CLI is real and honest. The memory engine, LangGraph chat graph,
JEV integrations, HTTP API and Telegram bridge were **not** changed.

### Added

- **The CLI** (`src/iris_ai/cli/`) — `iris --help` / `-h`, `iris version` /
  `--version` / `-V`, `iris doctor`, and a global `--debug`. Entry point
  `[project.scripts] iris = "iris_ai.cli.main:app"`; new deps `typer>=0.12`,
  `rich>=13`. Help lists only commands that exist — there is deliberately **no**
  `chat` stub.
- **`iris doctor`** — offline checks for `.env` presence (file merged under the
  process environment), package importability, provider key *names*, and
  `TYPESAFE_API_KEY`. Prints names and `set` / `missing` only, never values;
  exits `1` only when a check **fails**; `--debug` / `IRIS_DEBUG=1` re-raises so
  a crash produces a real traceback instead of the friendly hint.
- **`/mind` returns `staged`** — the newest `workspace/.dreams/staging-*.jsonl`
  signals, so the dream pipeline's pending work is inspectable through the API.
  Covered by the relocated `tests/test_staged_preview.py` (2 tests).
- **Phase documents** — [`docs/blueprint.md`](docs/blueprint.md) (all phases,
  gates, risk register), `docs/superpowers/specs/2026-09-22-p1-skeleton-library-cli-design.md`,
  `docs/superpowers/plans/2026-09-22-p1-skeleton-library-cli.md`, and
  `docs/superpowers/progress/p1-execution.md` (status + verification log).

### Removed

- **The web dashboard** — the whole `dashboard/` tree (FastAPI app, templates,
  static JS/CSS, its Dockerfile and requirements), `docs/console.md`, the three
  console screenshots, and the compose `dashboard` service.
- **Dashboard credentials** — `DASHBOARD_USER` / `DASHBOARD_PASSWORD` from
  `.env.example` and CI. There is no longer a web surface to log into.
- **`tests/test_console_fixes.py`** — three of its tests pinned deleted
  dashboard HTML/JS/proxy routes. The two tests that covered the *library*
  helper `iris_ai.api._staged_preview` were relocated to
  `tests/test_staged_preview.py` first, so no library coverage was lost.

### Changed

- **README rewritten** for the library + CLI shape: status and P1–P8 roadmap,
  CLI surface, quickstart (`uv sync` → `iris --help` → `iris doctor`), and
  console/dashboard claims removed. The engine sections (memory model, turn
  pipeline, JEV, recall lanes, dreaming, providers, observability) are kept,
  with the dashboard node dropped from every diagram.
- **`tests/test_security.py`** — the `dashboard.app` proxy-forwarding test is
  gone; iris-core route auth (by introspection) and Telegram bridge token
  forwarding are unchanged.
- **`pyproject.toml`** — added `typer` / `rich` and the console script; ruff's
  `src` list is `src`, `tests`, `scripts`, `mcp_servers` (no `dashboard`).
- **`docker-compose.yml` / CI / `.env.example` / `docs/deployment.md`** —
  dashboard references swept out; compose services are now `postgres`,
  `iris-core`, `telegram-mcp`.

### Tests

- **239 collected.** `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py`
  → **234 passed** locally, no API calls, no database.
- The 5 tests in `tests/test_memory_pipeline.py` are pgvector integration tests;
  they fail loudly with the fix command when no database is listening (they do
  not silently skip). CI starts `pgvector/pgvector:pg16` and runs all **239**.
- New: `tests/test_cli.py` (16 tests — help command set, version shapes,
  doctor levels/exit codes, `.env`-only keys, secret redaction, both `--debug`
  paths). Relocated: `tests/test_staged_preview.py` (2).
- Deleted: 3 dashboard-only tests (reasons above).

## Unreleased — latency + judgment observability pass

*The console-UI bullets from this pass were dropped in P1, along with the
dashboard they described. What survives is the library-side work.*

### Added

- **A judgment log, and the data behind it.** `src/iris_ai/turnlog.py` records
  per-turn what the judgment layer decided and how long each stage took;
  `src/iris_ai/agent/chat.py` writes both into `config/traces.jsonl` (`events`,
  `counts`, `stages_ms`). "Not checked" is recorded as explicitly as "checked
  and clean".
- **`GET /jev`** (authenticated) — judgment-layer health: enabled or not, why
  not, request/failure counters, last latency and last error. `/health` exposes
  the same block as `judgment`, alongside `background.pending`.
- **`src/iris_ai/background.py`** — tracked fire-and-forget tasks with `drain()`,
  because a bare `asyncio.create_task` can be garbage-collected mid-flight.
- **`/mind` returns `agents` (AGENTS.md), `daily` and `today`** — the persona
  file and the episodic tier were missing from the snapshot, so the API could
  report what Iris believes but never what she was told today.

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

## Unreleased — modernization pass (branch `refactor/modernize-jev`)

### Added

- **JEV (TypeSafe System One) as a typed-judgment layer** — `src/iris_ai/jev/`,
  three integrations, each behind an adapter with a deterministic fallback:
  recall reranking (`recall.py`, composed as `relevance × decay × importance`,
  never overriding the forgetting policy), skill selection (`skills.py`), and
  instruction-injection screening for untrusted content (`guard.py`). With
  `TYPESAFE_API_KEY` unset the stack behaves exactly as before. See
  [`docs/jev.md`](docs/jev.md).
- **Capture shows up in the turn trace** — `config/traces.jsonl` records what
  the capture node wrote (`capture`, empty when the prefilter or the judgment
  declined), returned by `GET /traces` as `💭 [importance] fact`.
- **Capture node** (`src/iris_ai/memory/capture.py`) — the write-path safety net.
  A deterministic prefilter keeps trivial turns free; one judgment (JEV, else
  the cheap tier) decides whether a turn holds a durable fact; the result is an
  ADD-only `(note)` line that still has to pass dreaming's Light-phase gate.
- **Owner gate on `/voice`** — the endpoint trusted a caller-supplied
  `user_id` as the graph session key.
- **Repo lint config** (`ruff`) and **CI** (`.github/workflows/ci.yml`): ruff,
  the full test suite against a real `pgvector/pgvector:pg16` service, and a
  production image build that asserts the container is non-root.
- **Test coverage for the new paths** — `tests/test_jev.py` (22),
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
- `workspace/skills/*` is gitignored (learned skills are personal data) — a
  blanket `git add` can no longer publish them.
