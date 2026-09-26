# P8 Execution Progress

**Plan (design + tasks):** `docs/superpowers/plans/2026-09-25-p8-ship.md`
**Audit:** `docs/superpowers/specs/2026-09-24-principles-conformance-audit.md` (gaps: G1 no pre-tool loop/spiral guard, G2 no per-tool cascade breaker, G3 budgets per-turn only, G4 approval not bound to anything, G6 eval has no statistics)
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-25

## Gate status

P4's DoD is still unticked, and P5–P7 ran on the owner's standing instruction of
2026-09-24 to finish every phase. That waiver is recorded in `p5-execution.md` and
applies here. No checklist is retro-ticked.

**Docker:** out of bounds by owner instruction. The five `test_memory_pipeline.py`
tests stay CI-verified. **No commits.**

**A note on shape:** P8 is the phase where the audit's four harness gaps stop being
*absences* and the eval stops reporting points. Two of the three jobs are guard
work with no user-visible surface, which is why they land here — P5.1 deferred them
deliberately to be built together with the eval discipline rather than as an
interstitial phase.

## What already existed (and was therefore not rebuilt)

- **The hook was already there.** P6's trace content policy replaced raw
  `tool_call.args` with `args_hash` (audit G5). That hash is exactly what loop
  detection needs, so P8 reads arguments through the same normalisation the trace
  uses instead of adding a second representation that could disagree with it.
- **Per-turn budget state existed** (P5's `TurnBudget`). P8 does not replace it —
  the turn ceiling is read from the live turn log, and only the **day** counters
  are owned by the new module, because nothing else survives a turn.
- **The JEV client already resets itself** after a failure. What did not exist was
  *per-tool* failure memory; G2 adds it rather than duplicating the client's retry
  logic.
- **`scripts/eval_lab.py` already measured recall@k, MRR and cost** across
  ablations. P8 changes how those measurements are *reported*, not what is measured.

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline | done | P7 exit: ruff clean, **684 passed** |
| 1 | `src/iris/guards.py` — spiral/dedup + cascade breaker + the ordered chain (TDD) | done | `arg_tokens` / `arg_similarity` / `SpiralDetector` / `CircuitBreaker` / `GuardChain`; `tests/test_guards.py` (19) |
| 2 | `src/iris/budget.py` — scoped counters split by kind, versioned policy (TDD) | done | `CounterKind`, `BudgetPolicy`, `Budget`; `tests/test_budget.py` (11) |
| 3 | Wire the chain into `ChatGraph` pre-dispatch | done | `self.guards.before/record/after`, `reset_turn()`; `tests/test_guard_wiring.py` (6) |
| 4 | `src/iris/approval.py` — digest binding, replay guard, terminal-state guard, fail-closed (TDD) | done | `effective_digest`, `Envelope`, `ReplayGuard`, `ApprovalGate.verify`; `tests/test_approval.py` (13) |
| 5 | Wire digest + `call_id` into the interrupt payloads and `resume` | done | `forget` / `skill_run` / `computer` envelopes + `resume` refusal; `tests/test_approval_wiring.py` (5) |
| 6 | `src/iris/eval/stats.py` — CI, noise floor, sample sizing, κ, decision rule (TDD) | done | `wilson_interval`, `bootstrap_mean_ci`, `paired_difference_ci`, `noise_floor`, `samples_needed`, `cohens_kappa`, `DecisionRule` / `decide`; `tests/test_eval_stats.py` (25) |
| 7 | Wire statistics into `scripts/eval_lab.py` | done | `render_report(...)` is a pure function; the report carries intervals + the noise floor; `tests/test_eval_stats.py::test_the_report_carries_intervals_not_points` |
| 8 | Ship polish — `uv build` in CI, console entry smoke, support matrix, sample config, version discipline | done | CI `package` job (`uv build` → install → `iris version` / `iris --help`), `docs/support.md`, `.env.example`, `tests/test_packaging.py` (6) |
| 9 | Docs + this log + DoD | done | README / CHANGELOG / blueprint; this file |

## The guard chain, and why its order

    budget → circuit → spiral/dedup → context → record

Each guard can only ever **refuse**; none can re-open what another closed. The
order is not cosmetic:

- **Budget first**, because it is the cheapest reason to stop and the only one that
  bounds *spend* rather than *behaviour*.
- **Circuit before spiral**, because a retry storm should be attributed to the tool
  that is failing rather than counted as repetition — the tests assert exactly this
  ordering (`test_the_budget_guard_fires_before_the_spiral_guard`,
  `test_a_retry_storm_is_refused_by_the_circuit_not_the_spiral_guard`).
- **Context**, because the refusal string carries what to do *instead*: a guard that
  answers only "no" teaches the model to retry.

Everything in the chain is deterministic and model-free — no JEV call, no LLM call,
no network. A guard that needs a model to decide whether to spend money is itself
capable of running away.

## Approval invariants

1. The envelope carries the **effective digest of the arguments after edits**, so
   "approve" is bound to a specific action, not a slot in a conversation.
2. One `tool_call_id` may be granted **once per thread**; a double-submitted resume
   is refused with a reason rather than read as a second deliberate grant.
3. A resume with **no approval waiting** is refused (terminal-state guard) —
   resuming a finished turn is a bug or an attack, not an approval.
4. **Fail closed**: an envelope for a side-effecting action with no digest is
   refused whether the owner said yes or no.

## Verification log

All commands from the repo root, 2026-09-25.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | **`769 passed`** (684 → 769: +85 across guard chain, budget, approval, guard/approval wiring, eval statistics and packaging) |
| `uv run pytest tests/test_guards.py tests/test_budget.py tests/test_approval.py tests/test_approval_wiring.py tests/test_guard_wiring.py tests/test_eval_stats.py tests/test_packaging.py -q` | `85 passed` |
| `uv run pytest tests/test_packaging.py -q` | `6 passed` — one version source, console entry, support matrix, every `.env.example` key names a real setting, and the P8 knobs are documented |
| `uv run iris --help` | commands are `agents`, `chat`, `cron`, `doctor`, `skills`, `tools`, `version` |

**The suite is warning-free, and that took a fix.** The warning that carried over
from P7 (`coroutine 'VertexLLM.async_completion' was never awaited`) turned out not
to be a thread-pool artefact but a **test double reaching the network**:
`LLMClient` is a real client, the journal's reflection pass fires on every
owner turn that retrieved memory, and the doubles in `tests/test_guard_wiring.py`
and `tests/test_subagents.py` overrode only `complete_with_tools` — so a unit test
inherited a live provider call (and left a LiteLLM coroutine for the closed loop
to drop). Both doubles now answer `complete` offline, and `tests/fakes.py` states
the rule for future ones: a double overrides `complete` as well. The suite's
"deterministic, no API calls" claim is now true rather than aspirational.

Per-file counts for the new suites: `test_guards` 19, `test_budget` 11,
`test_approval` 13, `test_approval_wiring` 5, `test_guard_wiring` 6,
`test_eval_stats` 25, `test_packaging` 6.

## DoD checklist (owner must tick)

- [x] The guard chain runs **before dispatch**: a refused call never reaches
      `dispatch`, asserted by a test rather than by reading the call site
- [x] The chain order is enforced — budget fires before spiral, circuit fires
      before spiral
- [x] The same tool with near-identical arguments is refused on the declared
      repeat; case and whitespace differences do **not** disguise a loop
- [x] Varying arguments are **not** a spiral (no false positive on a legitimate
      multi-call turn)
- [x] Two consecutive failures of the same tool open that tool's circuit for the
      run; one success resets the streak
- [x] Three distinct failing tools escalate the turn
- [x] The growth ceiling stops a turn whatever it is calling
- [x] A `0` ceiling means **no ceiling**, not "a limit of zero"
- [x] The per-turn ceiling refuses and names itself; the per-day ceiling survives
      a new turn **and a restart**
- [x] Counters are split by kind (input / output / cached / embedding /
      tool-schema), and the embedding tier is not counted as conversation
- [x] A stale day file is not today's spend; an unwritable one is reported, not
      raised
- [x] The budget policy version is recorded with the numbers it produced
- [x] Every refusal is recorded in the turn trace
- [x] `tool_guard_enabled=false` is a no-op chain, not a crash
- [x] The approval digest is a function of meaning, not key order, and never
      raises on an unserialisable value
- [x] An edited resume mismatches the digest; a replayed `call_id` is refused; the
      same `call_id` on another thread is **not** a replay; a cancelled decision
      does not consume the grant
- [x] A side-effecting approval with no digest is refused; a read-only one without
      a digest is still fine
- [x] Wilson brackets a coin flip around 0.5, and a perfect score still has a
      lower bound
- [x] The bootstrap is deterministic for a seed and its interval contains the mean
- [x] `samples_needed` **derives** the vault's ~63-per-arm number for Δ=0.02 at
      σ=0.04 rather than asserting it
- [x] A rule that cannot pass reports `inconclusive`, not a pass; an invalid
      direction is rejected at construction
- [x] The eval report carries intervals and a noise floor, not points
- [x] The wheel's console entry is a CI job, and the sample config's every key
      names a real setting
- [x] README / CHANGELOG / blueprint all state P8 shipped with the same count
- [ ] Owner confirmation

## Deviations, and why

1. **The day budget persists to `config/budget.json`, not to Postgres.** Iris
   already keeps a durable local store for cron (`config/tasks.json`) and the trace
   file, and the day ceiling's job is to survive *a restart*, not to be queried by
   a fleet. A single file is the smallest thing that satisfies the requirement, and
   `tests/test_budget.py` covers the stale-day and unwritable-file cases
   explicitly.
2. **The statistics module is stdlib-only.** `statistics.NormalDist` gives the
   quantile, and the bootstrap is twenty lines. Adding SciPy for a Wilson interval
   would make the eval gate's dependency graph heavier than the eval. Verified by
   hand-computed cases (`test_cohens_kappa` at chance and at perfect agreement).
3. **The eval lab reports a sampling interval, and states its run-to-run noise
   floor is zero.** The lab is deterministic by construction (hash embeddings, no
   model calls), so `noise_floor()` over repeats is genuinely `0` and the report
   says so rather than fabricating a spread. The report text names the condition
   under which that changes: once a config includes a model (rerank, a judge),
   repeat it 3–5× and report the measured floor.
4. **No Textual TUI.** The plan marked it optional "only if still desired"; the
   library + CLI surface is the deliverable and a TUI would be a second client to
   keep honest. Nothing in the audit asks for it.
5. **Fail-closed applies to side-effecting envelopes only.** A read-only approval
   with no digest is still granted: refusing it would make the guard refuse
   everything with no upside, and the invariant is about actions that change the
   world.
6. **The runaway simulation is a test in the existing `test` job, not a new CI
   job.** The plan's wording was "CI runs a runaway simulation that asserts the
   refusal". `test_a_runaway_loop_is_refused_not_merely_observed` and
   `test_a_spiralling_call_is_refused_before_dispatch` assert the refusal and run
   under the job that already exists, so there is one place tests run rather than
   two.
7. **The guard chain is per-graph and held on the runtime.** `reset_turn()` clears
   exactly the turn-scoped state and leaves the day counters alone — the split is
   asserted (`test_reset_turn_clears_the_turn_scoped_state_only`,
   `test_the_day_budget_survives_a_reset_between_turns`).

## Findings

1. **The refusal had to come before the spend, not after.** Everything `iris` could
   already do about a runaway loop was observational — the trace recorded it. The
   audit's $-per-30-seconds framing is about the gap between observing and refusing,
   and closing it needed no model, only a pre-dispatch chain.
2. **One budget total cannot answer "what spent it".** Split counters are what make a
   budget report actionable: output tokens running away is a loop, tool-schema tokens
   are overhead, cached tokens are the cheap ones. The single number hid which one
   moved.
3. **An unbound approval is not an approval.** The interrupt carried the arguments
   but nothing pinned them, so an edited resume was indistinguishable from the
   original. The digest is small, cheap, and makes the word mean what it says.
4. **A point estimate on a six-query set is not a measurement.** The eval lab's
   ablations were already honest about *method*; P8 makes them honest about
   *uncertainty*, and adds the calibration (κ) that says whether a judge's
   judgments can be trusted at all.
5. **A test that reaches a provider is not a unit test.** The last warning in the
   suite was the canary for it: the fake's missing `complete` was not a testing
   detail, it was a live call in a suite whose README promises no API calls.

## Notes and follow-ups

- **Every audit gap now has a landing place.** G1/G2 → `guards.py`, G3 → `budget.py`,
  G4 → `approval.py`, G5 → P6's trace content policy, G6 → `eval/stats.py`. Hand the
  audit to a reader who wants to check the claim.
- **Deliberately still out of scope, stated rather than forgotten:** a gateway/proxy
  enforcement tier (single process, one owner), OpenTelemetry export (the span
  *fields* matter more than the wire format), a Wasm/microVM isolation tier (named in
  P7 as the prerequisite for lifting `computer_allow_owner_scripts_only`), and
  canary/shadow traffic splitting (there is no live user base to split).
- **Docker remains out of bounds**, so `docker build` and the five Postgres tests are
  CI-verified only.
- **The Postgres-backed eval numbers have not been re-run in this environment.**
  `scripts/eval_lab.py` needs `IRIS_EVAL_POSTGRES_DSN`; the *statistics* it now uses
  are verified by unit tests over synthetic measurements, and the wiring is verified
  by rendering the report function directly. Re-running the live lab is an owner
  action against a database.
