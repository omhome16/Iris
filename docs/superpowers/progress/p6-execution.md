# P6 Execution Progress

**Plan (design + tasks):** `docs/superpowers/plans/2026-09-24-p6-cron.md`
**Audit:** `docs/superpowers/specs/2026-09-24-principles-conformance-audit.md` (item G5 lands here)
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-24

## Gate status

P4's DoD is still unticked and P5's checklist is open. The owner gave a standing
instruction on 2026-09-24 to finish every phase; that waiver is recorded in
`p5-execution.md` and applies here. Neither phase's checklist is retro-ticked.

**Docker:** out of bounds by owner instruction. The five `test_memory_pipeline.py`
tests stay CI-verified. **No commits.**

**A note on process:** this phase followed a **conformance audit** the owner asked
for — Iris checked against their AI-Mastery vault (agent graph, memory, context,
tools, budgets, observability, eval, security) before continuing. The audit found
six gaps and no wrong turns; G5 (trace content policy + redaction) landed here.

## What already existed (and was therefore not rebuilt)

`src/iris_ai/tasks.py` already had the one-off half, and it is good: `parse_when`
(ISO / relative / shorthand), `TaskStore` (JSON under `workspace/config/`), and a
`TaskScheduler` that re-registers pending tasks at boot, runs each instruction
through the chat graph with `origin="task"` (so a scheduled run can never write
durable memory), delivers the reply over Telegram, and removes the task.
`src/iris_ai/scheduler.py` holds two hardcoded recurring jobs.

So P6 was not "build a scheduler". It was: **make recurrence and failure policy
first-class, make the trace policy explicit, and give the owner a way to see and
change the schedule.**

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline | done | P5 exit state: ruff clean, 515 passed |
| 1 | Job kinds: interval + calendar (TDD) | done | `parse_every`, `parse_calendar`, `Task.kind/every/at/weekdays`; `tests/test_cron_jobs.py` |
| 2 | Catch-up / misfire policy, declared (TDD) | done | `missed_decision()` as the single decision point; run/miss/failure counters; self-disabling jobs |
| 3 | `iris cron list \| add \| rm` (TDD) | done | `src/iris_ai/cli/cron.py` + `cron` command; `POST /cron/reload`; `tests/test_cron_cli.py` |
| 4 | Trace content policy + redaction (audit G5) (TDD) | done | `src/iris_ai/redact.py`, `TraceLogger.record` policy gate; `tests/test_redaction.py` |
| 5 | Docs, progress, DoD | done | README / CHANGELOG / blueprint updated; this log |

## Verification log

All commands from the repo root, 2026-09-24.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | **`596 passed, 1 warning`** (515 → 596: +35 cron jobs, +23 cron CLI, +22 redaction, +1 trace policy) |
| `uv run pytest tests --collect-only -q` | `601 tests collected` (596 runnable here + the 5 Postgres ones) |
| `uv run iris --help` | commands are `agents`, `chat`, `cron`, `doctor`, `skills`, `version` |
| `uv run iris cron list` (empty workspace) | `no scheduled jobs`, and it says where the built-ins live instead of pretending there is nothing |

The three tests that had to change are all assertions the phase *intends* to
change: the two command-set checks (now six commands) and
`test_chat_turn_records_trace`, which asserted the trace contained the raw user
message — under a metadata default it now asserts the hash/length **and** a new
test pins that `trace_content = redacted` brings the text back.

## DoD checklist (owner must tick)

- [x] Recurring jobs exist as first-class definitions (interval + calendar) beside
      one-off tasks, in one store and one run path
- [x] A `tasks.json` written before P6 loads unchanged as a `once` job
- [x] The misfire policy is a declared, tested function — and a job that was down
      for a day fires **once**, never once per missed window
- [x] A stale one-off is recorded as missed before it is dropped (the silent
      delete is gone)
- [x] A recurring job that keeps failing disables itself rather than retrying forever
- [x] `iris cron list | add | rm` works with no engine running, needs no broker,
      and reuses the agent's parsers rather than inventing a second grammar
- [x] `POST /cron/reload` lets a live engine pick up CLI changes without a restart
- [x] Trace content policy is explicit and defaults to metadata; credentials never
      reach the trace file in any mode, and the regression that started this
      (raw `tool_call.args` written to disk) has a test
- [ ] Owner confirmation

## Deviations, and why

1. **No separate P6 design spec.** The plan doubles as the design because the
   design *is* the policy table — three job kinds and three misfire outcomes are
   fully specified by the tables in `plans/2026-09-24-p6-cron.md`. Writing a second
   document restating them adds a place for the two to disagree.
2. **The class is still `Task`, and the endpoint is still `/tasks`.** "Job" is the
   word in the docs; renaming the type, the tool, the API route, the bridge
   command and every test would be churn with no behaviour attached. The concept
   count is one either way.
3. **`redact()` is pattern-based and admits it.** It cannot be complete; what it
   guarantees is that the telemetry paths never *voluntarily* write a credential.
   Documented in the module docstring rather than oversold.
4. **`write_raw()` exists on the logger** for migrations/tests, with a docstring
   saying nothing in `src/` should call it. The alternative was to make the policy
   skippable by a flag, which invites exactly the drift the policy exists to stop.
5. **`coalesce=True` + an explicit `missed_decision()`** rather than relying on
   APScheduler's `misfire_grace_time` alone: the library default decided policy
   silently before, and the vault's rule is that bounds are declared and tested.

## Findings

1. **The trace wrote raw tool arguments to disk** — `_trace_turn` called
   `json.dumps(tc.get("args"))[:200]`, so any secret a model passed as an argument
   was persisted. Now an `args_hash` in metadata mode (which also makes loop
   detection possible later) and redacted in every mode.
2. **A stale one-off task vanished silently.** `register_all()` dropped past tasks
   with only a log line, so "why didn't my reminder fire?" had no answer. It is
   now counted (`missed`) and the decision recorded (`last_outcome`).
3. **Nothing redacted anything**: `grep -riE "redact|scrub" src/iris_ai` returned zero
   hits before this phase.

## Notes and follow-ups

- **Deliberately still open, tracked in the blueprint:** P5.1 harness hardening
  (G1 loop/spiral detection, G2 cascade breaker, G3 budget scopes, G4 approval
  digest + replay guard), P7's tool classes + surface strategy and the
  kernel-boundary decision for non-owner-authored code, and P8's eval statistics.
- **`iris cron add` from the CLI needs `POST /cron/reload`** (or a restart) to
  affect a running engine — stated in the CLI's own output, not just in the docs.
- **Still CI-only:** the five `test_memory_pipeline.py` tests (Postgres).
