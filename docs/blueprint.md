# Iris Blueprint — All Phases

> Source of truth for what Iris is becoming. Specs live under `docs/superpowers/specs/`; task plans under `docs/superpowers/plans/`. Execution status: one log per phase under `docs/superpowers/progress/` (`p1`–`p8-execution.md`).

**Shape:** Library (`import iris`) + thin CLI (`iris …`). In-place rebirth (no fork). Import path never changes.

**Best-practice basis:** audited against the owner's AI-Mastery vault on
2026-09-24 — see `docs/superpowers/specs/2026-09-24-principles-conformance-audit.md`
for what Iris already satisfies, the six gaps it did not, and where each gap
landed in the phases below.

**Global rules (all phases):**
- No commits unless the user explicitly asks.
- The per-phase DoD gate stands, but on 2026-09-24 the owner gave a standing
  instruction to finish every phase — recorded as an explicit waiver in each
  phase's execution log rather than silently ignored.
- Doctor/CLI never print secret values — names + `set`/`missing` only.
- Offline by default; network only where a phase explicitly requires it.
- Do not weaken library tests to get green.

---

## Phase overview

| Phase | Name | Deliverable | Gate to next |
|-------|------|-------------|--------------|
| **P1** | Skeleton + CLI | Dashboard deleted; `iris --help\|version\|doctor`; honest tests/CI/docs | User ticks DoD checklist |
| **P2** | Core brain ✅ | Library boot path (`iris.harness()`) + turn API + `iris chat`, with an honest degraded mode | P1 DoD green; P2 DoD verified |
| **P3** | Telegram ✅ | Bridge as first-class client on the library | P2 DoD green; P3 DoD verified |
| **P4** | Skills ✅ | Registry across 4 sources, Agent Skills manifests, `allowed-tools` policy, JEV-gated script boundary | P3 DoD green; P4 DoD checklist green except owner confirmation |
| **P5** | Multi-agent ✅ | Lead + researcher + critic, typed handoffs with provenance, code-owned budgets, 2 JEV judgments, `iris agents` + `GET /agents` | P4 DoD still open — **owner waived the gate** on 2026-09-24 |
| **P6** | Cron ✅ | Interval + calendar + one-off jobs in one store, a declared misfire policy, `iris cron` + `POST /cron/reload`, trace content policy + redaction | owner waived the gate on 2026-09-24 |
| **P7** | Computer-use ✅ | One `computer` tool (screenshot / navigate / click / type), declared tool classes + a visible-surface budget, an action audit log, `iris tools` + `GET /tools` + `GET /actions` | owner waived the gate on 2026-09-24 |
| **P8** | Ship ✅ | Pre-tool guard chain (spiral + cascade breaker), scoped split budgets, approval integrity, eval statistics, packaging verified by CI | owner waived the gate on 2026-09-24 |

---

## P1 — Library-first skeleton + CLI shell ✅ (implemented 2026-09-23; owner verification outstanding)

**Goal:** Kill the web dashboard; ship a real offline CLI on a clean library package.

**Features:**
- `iris --help` / `-h` — rich-styled help; only real commands (`version`, `doctor`); no fake `chat` stub.
- `iris version` / `--version` / `-V` — version, Python, package path.
- `iris doctor` — offline checks: `.env` presence, package import, provider key names, `TYPESAFE_API_KEY`; exit `1` only on fail; `--debug` / `IRIS_DEBUG=1` re-raises for traceback.
- `[project.scripts] iris = "iris.cli.main:app"`; deps `typer>=0.12`, `rich>=13`.

**Removed:**
- `dashboard/` tree, `docs/console.md`, console screenshots/logs.
- compose `dashboard` service; `DASHBOARD_USER` / `DASHBOARD_PASSWORD` from `.env.example` + CI.
- Dashboard-only tests; `_staged_preview` library tests preserved in `tests/test_staged_preview.py`.

**Unchanged:** agent graph, memory algorithms, JEV tools, Telegram bridge behavior, `iris.api` routes (except staged-preview library work).

**Layout:**
```
src/iris/
  cli/
    __init__.py
    main.py          # typer root, --debug, -h/--help
    help_theme.py    # rich theme
    doctor.py        # pure checks + _load_dotenv
    version.py       # version_lines()
  … existing library …
```

**DoD (user must verify):**
- [ ] `uv sync` then `uv run iris --help` shows only `version` + `doctor`
- [ ] `uv run iris -h` same as `--help`
- [ ] `uv run iris version` → three lines
- [ ] `uv run iris doctor` → names only, exit 0 unless fail
- [ ] `uv run iris doctor --debug` → traceback on crash
- [ ] `Test-Path dashboard` → False
- [ ] `uv run ruff check .` clean
- [ ] `uv run pytest -q` green; count matches CHANGELOG
- [ ] README quickstart works from clone

---

## P2 — Core brain (chat) ✅ (implemented 2026-09-23)

**Goal:** Make the library the product’s mind; CLI becomes a real client.

**Shipped** (spec: `docs/superpowers/specs/2026-09-23-p2-core-brain-chat-design.md`,
plan: `docs/superpowers/plans/2026-09-23-p2-core-brain-chat.md`, log:
`docs/superpowers/progress/p2-execution.md`):
- `src/iris/engine.py` — `harness()` / `Harness`, the single boot path; `api.py`
  is now a thin client of it (`postgres="require"`, behavior unchanged).
- Public turn API: `respond()` / `resume()` / `stream()`; `iris.harness()` and
  `iris.Harness` are the only new public names.
- `iris chat` — streaming REPL, `--once`, `--session`, terminal HITL approval.
- Degraded mode: `postgres="auto"` keeps a session alive without a database
  (in-memory threads, no recall, loud banner + an actionable recall error).
- Tests 234 → 255 runnable (260 collected); docs, CHANGELOG and the learning
  vault updated.

**Planned features:**
- Turn pipeline as library API (ingest → retrieve → judge → respond).
- `iris chat` — REPL/stream over the same library path the API uses.
- Provider abstraction: Gemini / Groq / OpenRouter (keys already named in doctor).
- Streaming responses; session/memory continuity via existing memory model (Markdown soul + pgvector).
- Judgment layer + reflection kept off the hot reply path (existing behavior preserved).

**Out of scope:** Telegram, skills execution, multi-agent, cron, computer-use.

---

## P3 — Telegram ✅ (implemented 2026-09-23)

**Goal:** Telegram as first thin client on the library brain.

**Shipped** (spec: `docs/superpowers/specs/2026-09-23-p3-telegram-client-design.md`,
plan: `docs/superpowers/plans/2026-09-23-p3-telegram-client.md`, log:
`docs/superpowers/progress/p3-execution.md`):
- `src/iris/channels/brain.py` — `BrainClient` protocol, `HttpBrainClient`,
  `BrainEvent`, `parse_sse_line()`: one HTTP/SSE contract for every client.
- `src/iris/channels/updates.py` — `normalize_update()` + `UpdateLedger`
  (persisted, bounded, atomic), so a restart cannot replay updates into turns.
- The bridge keeps only transport (polling, sending, progressive edits, typing,
  downloads, owner gate) and calls the library for everything else.
- Fixed `MemoryHit.chunk_index`: the two-phase `/forget` confirm path could
  never succeed before it (`AttributeError` on every real hit).
- Tests 255 → 294 runnable; bridge image installs the library with `--no-deps`.

**Deferred (deliberately, see the spec):** an in-process `LibBrainClient`
(no HTTP hop for same-host deployments) and webhook mode — long-poll + the
ledger covers the single-owner case, and neither changes the contract.

**Out of scope:** multi-bot fleets, skills, cron UI.

---

## P4 — Skills ✅ (implemented 2026-09-23)

**Goal:** Pluggable capabilities with safe loading and clear registry.

**Shipped** (spec: `docs/superpowers/specs/2026-09-23-p4-skill-registry-design.md`,
plan: `docs/superpowers/plans/2026-09-23-p4-skill-registry.md`, log:
`docs/superpowers/progress/p4-execution.md`):
- One `Skill` with a manifest; two encodings normalized into it — Iris's flat
  sidecar pair and the open Agent Skills `SKILL.md` layout.
- `SkillRegistry`: four sources in precedence order (workspace learned →
  workspace directories → extra dirs → entry-point packages → repo builtins),
  reported conflicts instead of silent shadowing, validation against the real
  tool surface, deterministic order, no caching.
- `allowed-tools` enforced at tool dispatch: a skill may only *narrow* a turn,
  never widen it, and never re-open what the session rule closed.
- One path to code: `skill_run` → in-directory resolution → AST pre-screen →
  binding JEV judgment gate → owner approval carrying findings and args →
  subprocess with a constructed environment, timeout and capped output.
- `iris skills list | show | validate` (exit 1 on errors) and one shipped
  builtin skill proving the format end to end.
- Tests 294 → 410 runnable. Prompt-side injection (P2) now draws from
  `selectable()`, so a disabled or invalid skill is never named.

**Deliberately deferred (see the spec):** code-loading entry points (a skill's
logic stays declarative plus `scripts/`), non-Python runtimes, kernel/container
isolation, `iris skills enable|disable` (the manifest field exists), and
marketplace/signing.

**Out of scope:** marketplace, remote skill install.

---

## P5 — Multi-agent ✅ (implemented 2026-09-24; owner waived the P4 gate)

**Goal:** Orchestrator + specialist agents with explicit handoffs.

**What shipped:** `src/iris/agents/` — roles as declared data (a read-only
**researcher**, a heterogeneous **critic**), a typed `Handoff` that carries
provenance (an unsourced claim is marked, never asserted), a `RoleRunner` that
generalized the shipped research subgraph, and an `Orchestrator` whose policy is
entirely code-owned: 2 calls per turn, a 20 s section deadline, fan-out gated by
a JEV effort judgment, one revision gated by a JEV sufficiency judgment, and a
deterministic provenance-preserving merge. `iris agents roles|show|handoffs` and
`GET /agents` make "which agent decided what" inspectable, and per-turn token
spend is recorded so the ~15x cost of multi-agent is visible where the decision
was made. The lead still executes and authors — there is no executor role.

**Planned features:**
- Roles (researcher, critic, executor, etc.) over the shared memory.
- Orchestrator policy: route, fan-out, merge.
- Handoff protocol + transcript visible in judgment/reflection.
- Cost/latency budgets; fallback to single-agent path.
- CLI/API observability: which agent decided what.

**Out of scope:** cross-org agents, durable workflow engine.

### P5.1 — Harness hardening (audit-driven) — **landed in P8**

Four gaps the conformance audit found, all of them *absences* rather than
mistakes. Each has a falsifiable acceptance test, and none reverses shipped
behaviour.

**Status:** not a gate. P6 shipped with this open, and it landed in the **P8
hardening pass** alongside the eval gates — all four items are guard/limit work
with no user-visible surface, so they were cheaper to land together with the eval
work than as an interstitial phase. Recorded in `progress/p6-execution.md`, and
now implemented: `progress/p8-execution.md` (`src/iris/guards.py`,
`src/iris/budget.py`, `src/iris/approval.py`).

**Features (all shipped in P8):**
- **A pre-tool guard chain in the documented order** — budget → circuit →
  spiral/dedup → context → record — evaluated pre-dispatch, outside the graph.
  Spiral signals with the vault's thresholds: *same tool + normalised args ≥3×*,
  argument **Jaccard > 0.72**, **>5–10 calls in a turn** when the normal is 1–3.
  This is the highest-ROI guard in the vault's own ranking (uncontrolled ≈ $2 /
  30 s vs circuit-broken ≈ $0.01).
- **A cascade breaker** — two consecutive failures of the same tool open the
  circuit for the rest of the run (`unavailable — do not retry`), and 3+ tools
  failing in one turn escalate it. The JEV client already reset itself; nothing
  per-tool did.
- **Budget scopes** — a per-day / cross-session ceiling so a loop cannot spend
  across turns, and counters split by kind (input / output / cached /
  embedding / tool-schema) rather than one total.
- **Approval integrity** — the approval envelope is bound to the **effective
  digest of the arguments after edits**, `tool_call_id` replay is guarded, and
  resuming a terminal run is refused. Before P8 the interrupt carried args but
  nothing pinned them.
- **A runaway simulation that asserts the *refusal*, not the alert** —
  `test_a_runaway_loop_is_refused_not_merely_observed` and
  `test_a_spiralling_call_is_refused_before_dispatch`, which run in CI's existing
  test job.

**Out of scope:** a gateway/proxy enforcement tier (single-process app), and
OpenTelemetry export (the span *fields* matter more than the wire format).

---

## P6 — Cron ✅ (implemented 2026-09-24 under the owner's standing waive-the-gates instruction)

**Goal:** Time as a first-class trigger.

**Shipped** (plan: `docs/superpowers/plans/2026-09-24-p6-cron.md`, log:
`docs/superpowers/progress/p6-execution.md`, final count: **596 passed**):
- **Three job kinds in one store** — `once`, `every <interval>`, and calendar
  (`daily at 09:00`, `mon,wed,fri at 18:30`). Recurrence is parsed by the same
  grammar the agent's `schedule_task` tool uses (one grammar, not two), and a
  pre-P6 `tasks.json` still loads unchanged as a `once` job.
- **A declared misfire policy, not a library default.** `missed_decision()` is
  the single decision point: a job whose window was missed while Iris was down
  fires **once** (coalesced), a stale one-off is recorded as `missed` before it
  is dropped (the silent delete is gone), and a recurring job that keeps failing
  disables itself rather than retrying forever.
- **`iris cron list | add | rm`** — read-only `list` works with no engine
  running and needs no broker; `add` writes the same store the agent writes, so
  there is no second configuration surface. `POST /cron/reload` lets a live
  engine pick up CLI changes without a restart (and the CLI says so).
- **Trace content policy + redaction (audit G5).** Scheduled runs happen with
  nobody watching, so `TraceLogger.record` now applies an explicit policy:
  `metadata` (default), `redacted`, `sampled`, `full`. Credentials never reach
  the trace file in any mode; `args_hash` replaces raw arguments, which is both a
  privacy fix — `_trace_turn` was writing raw `tool_call.args` to disk — and the
  hook loop/spiral detection will need later.

**Verification:** `uv run iris cron list` on an empty workspace prints `no
scheduled jobs` and points at the built-ins rather than pretending there is
nothing; `uv run iris --help` shows `agents`, `chat`, `cron`, `doctor`, `skills`,
`version`. Doctrine check: the vault's rule is that timeouts and bounds are
**declared and tested**, which is why the misfire policy is a function with a
unit test per outcome rather than an APScheduler kwarg.

**Out of scope:** distributed leaders, HA scheduler, a remote trace backend.

---

## P7 — Computer-use ✅ (implemented 2026-09-25 under the owner's standing waive-the-gates instruction)

**Goal:** Iris can operate a desktop/browser when granted.

**Shipped** (plan: `docs/superpowers/plans/2026-09-24-p7-computer-use.md`, log:
`docs/superpowers/progress/p7-execution.md`, final count: **684 passed**):
- **Declared tool classes that drive policy (audit).** Every tool declares a class
  (`read` / `filesystem` / `memory_write` / `network` / `credentialed` /
  `delivery` / `control`) and the class yields a default policy (`allow` / `ask` /
  `deny`). Resolution is **most-specific-wins** with **`deny` always winning**, so
  a class-wide deny cannot be re-opened one tool at a time. Two coverage tests
  pin the table to `TOOL_NAMES` in *both* directions, so the failure mode that
  made the old hand-maintained allowlists dangerous — a new tool arriving
  unclassified — is now a red test.
- **A visible-surface budget + `find_tools`.** The vault's guidance is ≤20 visible
  tools, namespacing from 20–100. P7 declares each tool `core` (never deferred) or
  `extended` (deferrable) and defers `extended` tools, last-declared first, past
  `tool_surface_budget` (default 20). Deferral is **presentation, not permission**:
  a deferred tool is still callable, `find_tools` returns its schema, core tools
  are never hidden at any budget, and a connected channel's tools are promoted
  past the budget.
- **One `computer` tool, not five.** `computer(action=screenshot|navigate|click|type)`
  keeps new capability inside one schema name, and is registered **only when
  `computer_enabled`** — off means the capability is absent, not present-but-refusing.
- **A permission model that is the actual boundary.** Suffix-matched host and
  title allowlists (label-boundary matching: `example.com` does not match
  `evil-example.com`), unconditional confirmation for the destructive subset
  (`click`/`type`, and any keystroke into a credential-looking field), and a
  per-session grant with an action budget so one approval cannot authorize an
  unbounded loop.
- **An action audit log that never records what was typed.** Append-only
  `config/actions.jsonl` through P6's redaction; a `type` records a length and a
  digest, never the text — an action log containing typed text is a keylogger.
  Every attempt (refused, cancelled, budget-exhausted or performed) produces
  exactly one record.
- **A provider boundary that refuses instead of exploding.** A missing driver is
  an `unavailable` refusal, not an `ImportError` mid-turn; `NullProvider` is the
  default and a driver that raises never raises into the graph.
- **`iris tools` / `GET /tools` / `GET /actions`** — the policy readout (class,
  resolved policy, source, deferral) and the action log, so a decision nobody can
  inspect cannot pretend to have happened.

**Deviation, stated:** the permission model is **owner-authored-only by policy**
(`computer_allow_owner_scripts_only`, default true). The vault's position is that a
container is not a containment boundary for untrusted code, so the Wasm/microVM
tier is named as the prerequisite for lifting it — not implied by `subprocess`.

**Out of scope:** unattended production RPA, credential vaults, a Wasm/microVM
isolation tier.

---

## P8 — Ship ✅ (implemented 2026-09-25 under the owner's standing waive-the-gates instruction)

**Goal:** Someone else can install and trust Iris.

**Shipped** (plan: `docs/superpowers/plans/2026-09-25-p8-ship.md`, log:
`docs/superpowers/progress/p8-execution.md`, final count: **769 passed**):
- **A pre-tool guard chain (audit G1–G3), in a fixed order.**
  `src/iris/guards.py` evaluates **budget → circuit → spiral/dedup → context →
  record** *before dispatch*, outside the tool and outside the graph, so a refusal
  costs nothing and never reaches a provider. Spiral detection uses the vault's
  thresholds (same tool + normalised args ≥3×, argument **Jaccard > 0.72**,
  more than the declared calls in a turn when the normal is 1–3); case and
  whitespace differences do not disguise a loop, and legitimately varying
  arguments do not trip it. Everything is deterministic and model-free — a guard
  that needs a model to decide whether to spend money can itself run away.
- **A cascade breaker (audit G2).** Two consecutive failures of the same tool open
  that tool's circuit for the rest of the run (`unavailable — do not retry`); three
  distinct failing tools escalate the turn. A success resets the streak, so a tool
  that failed once and then worked is not punished for a flake.
- **Scoped budgets with split counters (audit G3).** `src/iris/budget.py` counts
  input / output / cached / embedding / tool-schema tokens separately rather than
  as one total (they fail differently), enforces a per-turn ceiling and a per-day
  ceiling that **survives a restart** (`config/budget.json`), and carries a
  versioned policy so a number can be attributed to the policy that produced it.
  `0` means no ceiling, everywhere.
- **Approval integrity (audit G4).** `src/iris/approval.py`: the interrupt
  envelope carries the **effective digest of the arguments after edits**, so an
  approval is bound to a specific action rather than a slot in a conversation; one
  `tool_call_id` grants **once per thread**; resuming a thread with nothing waiting
  is refused rather than handed to the graph; and a side-effecting action with no
  digest **fails closed** whether the owner said yes or no.
- **Eval gates with statistics (audit G6).** `src/iris/eval/stats.py` — Wilson
  intervals for rates, a seeded bootstrap for means, a paired interval for
  "candidate vs baseline", a measured noise floor, sample sizing that **derives**
  the ~63-samples-per-arm figure for Δ=0.02 at σ=0.04, Cohen's κ for
  judge–human agreement, and a **pre-registered decision rule** that reports
  `inconclusive` rather than a pass when it cannot pass. `scripts/eval_lab.py`
  renders intervals and the noise floor instead of point estimates.
- **Ship polish.** One version source (`src/iris/__init__.py`, read by hatchling),
  a CI **`package` job** that builds the wheel, installs it and runs the console
  entry, a sample config whose every key names a real setting (asserted), and
  `docs/support.md` — the support matrix, verified axes, and how a release is cut.

**Out of scope:** PyPI marketing, multi-tenant hosting, shadow/canary traffic
splitting (there is one owner and no live user base to split), a Textual TUI (the
library + CLI is the surface; a TUI would be a second client to keep honest), a
gateway/proxy enforcement tier, and OpenTelemetry export (the span *fields* matter
more than the wire format).

---

## Cross-phase architecture (target)

```
Clients:     CLI (typer) · Telegram bridge · (optional TUI) · HTTP API
                ↓
Library:      turn pipeline · judgment · memory (Markdown + pgvector) · JEV
                ↓
Providers:    Gemini · Groq · OpenRouter · Tavily · Typesafe
                ↓
Extensibility: skills · multi-agent · cron · computer-use
```

**Compose services (P1+):** `postgres`, `iris-core`, `telegram-mcp` — no dashboard.

---

## Risk register (all phases)

| Risk | Mitigation |
|------|------------|
| Dashboard references creep back | P1 grep sweep; exclude only specs/plans/CHANGELOG |
| Secret values printed | Doctor tests + `repr=False` config fields |
| Fake CLI commands | Help test pins allowed command set |
| Tests dropped silently | Relocate before delete; CHANGELOG records counts |
| Phase skip / P2 early start | Hard gate: user verifies prior phase DoD |
| Provider key drift | Doctor checks names only; `.env.example` is template |

---

## Related documents

- Spec (P1): `docs/superpowers/specs/2026-09-22-p1-skeleton-library-cli-design.md`
- Plan (P1): `docs/superpowers/plans/2026-09-22-p1-skeleton-library-cli.md`
- Spec (P3): `docs/superpowers/specs/2026-09-23-p3-telegram-client-design.md`
- Plan (P3): `docs/superpowers/plans/2026-09-23-p3-telegram-client.md`
- Execution logs: `docs/superpowers/progress/p1-execution.md` …
  `docs/superpowers/progress/p8-execution.md` (one per phase)
- Older design history: `docs/superpowers/specs/2026-08-15-iris-design.md`
