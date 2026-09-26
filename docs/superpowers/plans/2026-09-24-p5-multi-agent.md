# P5 plan — multi-agent: roles, handoffs, orchestrator policy

**Spec:** [`2026-09-24-p5-multi-agent-design.md`](../specs/2026-09-24-p5-multi-agent-design.md)
**Branch:** `refactor/modernize-jev`
**Rules:** TDD — each task writes its failing tests first. No commits unless the
owner asks. **No Docker commands** (owner instruction, 2026-09-24): the
`test_memory_pipeline.py` Postgres suite stays CI-verified, and the runnable
suite is always `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py`.

## Global constraints

- **Back-compat is a test, not a hope.** `deep_dive` and `tests/test_subagents.py`
  must keep passing with no behavioural change; `multi_agent_enabled=false`
  reproduces the pre-P5 path exactly.
- **Roles only narrow.** A role's allowlist intersects the tool surface; it can
  never add a tool the lead cannot use.
- **JEV never owns a threshold.** Code compares and decides; both judgments fail
  open when JEV is unavailable.
- **Telemetry never breaks a turn** (the existing `turnlog` contract): handoff
  recording is best-effort and no-ops outside a turn.
- **No Docker, no network, no key in tests.** Every suite is fake-driven.

## Task 0 — Baseline

`uv run ruff check .` and the runnable suite, recorded before anything changes.
Expected P5 entry state: **410 passed**, ruff clean (P4 exit state).

## Task 1 — Roles as declared data (TDD)

`src/iris/agents/roles.py`: a frozen `Role` dataclass and the `researcher` /
`critic` declarations. Reuse the P4 precedent for allowlists rather than
inventing a second mechanism (`SkillPolicy` already intersects with the session
rules) — the role's `tools` is validated against the same `TOOL_NAMES` set that
manifests are checked against.

Tests (`tests/test_agent_roles.py`) written failing first: allowlists narrow;
an unknown tool name raises; tier/round/output caps are carried; the researcher's
toolset is exactly read-only and contains no mutating tool.

## Task 2 — The handoff protocol (TDD)

`src/iris/agents/handoff.py`: the `Handoff` dataclass, claim provenance, and a
`render_findings()` that formats claims for the lead as **data**.

Tests (`tests/test_handoffs.py`): round-trip; a claim with no source is marked
`unsourced`; `render_findings` never emits instruction-shaped framing (ingested
text stays quoted data); truncation marks itself; the refusal reason is a stable
string.

## Task 3 — The role runner (TDD)

Generalize `ResearchSubagent` into a bounded runner that takes a `Role`, keeping
the existing properties: no checkpointer, recursion cap, max tool rounds, tool
errors as JSON rather than exceptions, and a report that never raises into the
turn.

Tests (`tests/test_orchestrator.py`, part 1): a role runs on its declared tier;
the round cap stops the loop; a tool that raises yields a report, not a crash;
output is capped and marked.

## Task 4 — Orchestrator policy: route, budget, merge, fallback (TDD)

`src/iris/agents/orchestrator.py`: the code-owned policy — call cap, deadline,
stable merge order, the one-revision rule, and every degradation path.

Tests (`tests/test_orchestrator.py`, part 2): routing remains tool-initiated
(no regex trigger exists — asserted, since v2 removed it deliberately); the call
cap refuses with `budget_exhausted`; the deadline stops new handoffs; merge order
is deterministic; a breach returns what exists and the turn still answers;
`multi_agent_enabled=false` takes the old path.

## Task 5 — The two JEV judgments (TDD)

Effort (fan out?) and sufficiency (is the draft grounded?). Both ask a typed
question through `JevClient`, both compare in code against a config gate, both
fail open.

Tests: gate above/below behaviour with a stubbed JEV client; JEV absent → the
deterministic path; a low verdict triggers **exactly one** revision, never a
loop; `multi_agent_revise_once=false` skips it.

## Task 6 — Turn-scoped usage accumulator (TDD)

Mirror `turnlog`'s ContextVar pattern inside `LLMClient._record` so a turn can
report tokens per tier; `spend.tokens` on each handoff reads from it. Additive:
with no turn active it is a no-op, exactly like `turnlog.record`.

Tests: outside a turn, nothing is recorded; inside one, multi-agent spend is
visible; the ledger JSONL is unchanged (the accumulator observes, it does not
rewrite accounting).

## Task 7 — Observability: turnlog, CLI, API (TDD)

`record("handoff", ...)` / `record("agent_verdict", ...)`; `iris agents
roles|show|handoffs` (`src/iris/cli/agents.py` + a command in `cli/main.py`);
`GET /agents`.

Tests: the judgment block carries handoffs and verdicts; `iris agents` renders
and exits 0; the API route returns the pack plus recent handoffs; the command-set
assertion in `tests/test_cli.py:28` is updated to include `agents`.

## Task 8 — Docs, progress, DoD

README (status → P5, `iris agents` rows, a "Roles and handoffs" section with the
data-not-instructions note), CHANGELOG (P5 section with the measured numbers),
`docs/blueprint.md` P5 marked shipped, `docs/jev.md` integration **#5 and #6**
(the two judgments, and the note that neither is binding — both fail open),
`docs/superpowers/progress/p5-execution.md` (owner decisions, tasks, verification
log, DoD checklist, deviations), and this plan's spec cross-link.

## Out of scope (reject during review)

Cross-org agents; a durable workflow engine; roles defined in files; a separate
executor or planner role; a memory-curator role; autonomous/background
multi-agent runs; parallel fan-out beyond `multi_agent_max_parallel`; widening a
role's tool surface; and any Docker-based verification.
