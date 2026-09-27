# P5 Execution Progress

**Plan:** `docs/superpowers/plans/2026-09-24-p5-multi-agent.md`
**Spec:** `docs/superpowers/specs/2026-09-24-p5-multi-agent-design.md`
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-24

## Gate status (read this first)

**P4's DoD was never ticked.** Its only unchecked box is "Owner confirmation",
and its implementation is complete and verified. The owner instructed on
2026-09-24: *"proceed, I want you to finish every phase"* — a standing waiver of
the per-phase gate, recorded here rather than assumed. P4's checklist is left
untouched (not retro-ticked) and this phase's own DoD below waits the same way.

**Docker:** out of bounds by owner instruction ("docker is having an error …
don't use docker till then"). The five `test_memory_pipeline.py` tests need
Postgres and stay CI-verified; no `docker build` was run.

**No commits** — repo rule.

## Owner decisions (asked and answered before the work)

| Question | Answer | Consequence |
|---|---|---|
| Start P5? | Yes, now | The gate is waived explicitly, not ignored |
| Role pack | "Research the best pack for our project" | Researched and recommended: 3 parts, 2 new roles (below) |
| Routing | Code policy + JEV judgment | Bounds and actions in code; JEV supplies two probabilities and fails open |
| Observability | turnlog/trace + CLI + API | `iris agents` and `GET /agents` |

### The researched role pack

| Role | Tier | Why it exists |
|---|---|---|
| **Lead** (main agent) | strong | Orchestrator-worker is the pattern that works; the lead also **executes** and authors, so there is one writer and no second loop |
| **researcher** | cheap | The shipped worker, generalized. Read-only, escalation lane, 3 tool rounds |
| **critic** | strong | New. Read-only, **opposite tier** from the producer, per-claim verdicts |

Rejected, with reasons: an **executor** role (duplicates the lead's loop and adds
the inter-agent dependency the industry source calls a poor fit), a **memory
curator** (`capture`, `dreaming`, `reflection` and `skill_write` already do it), a
**planner** (ReAct is the planner; a planner role would restate the lead's own
decision at the cost of a call), and a **citation agent** (promoted to a protocol
rule instead — every claim carries provenance or is marked unsourced, which is
cheaper than an agent and cannot be skipped).

Sources behind the shape: Anthropic's multi-agent research system (orchestrator +
subagents, +90.2% on breadth-first research, parallel tool calls −90% wall clock,
**~15x the tokens of a chat**, and the explicit note that it fits badly when every
agent shares one context); "Large Language Models Cannot Self-Correct Reasoning
Yet" (Huang et al., ICLR 2024) and the self-preference-bias work (NeurIPS 2024) for
why the critic must be **heterogeneous** and evidence-bound; the production failure
taxonomy for why fan-out needs a gate rather than a default.

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline | done | P4 exit state: ruff clean, 410 passed (DB suite excluded) |
| 1 | Roles as declared data (TDD) | done | `src/iris_ai/agents/roles.py`; `tests/test_agent_roles.py` (18) |
| 2 | The handoff protocol (TDD) | done | `handoff.py`; `tests/test_handoffs.py` (15) |
| 3 | The role runner (TDD) | done | `runner.py` generalizes `ResearchSubagent`, which survives as a thin adapter — `tests/test_subagents.py` passes **unchanged** |
| 4 | Orchestrator policy (TDD) | done | `orchestrator.py`; `tests/test_orchestrator.py` (33) |
| 5 | The two JEV judgments (TDD) | done | `src/iris_ai/jev/agents.py`; `tests/test_jev_agents.py` (18) |
| 6 | Turn-scoped usage accumulator (TDD) | done | `turnlog.add_usage` ← `LLMClient._record`; `tests/test_turn_usage.py` (9) |
| 7 | Observability + wiring (TDD) | done | `iris agents`, `GET /agents`, `verify_answer`, `deep_dive` through the orchestrator; `tests/test_agents_cli.py` (12) |
| 8 | Docs, progress, DoD | done | README / CHANGELOG / blueprint / `docs/jev.md` (§3.4) updated; this log |

## Verification log

All commands from the repo root, 2026-09-24.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | **`515 passed, 1 warning`** — 410 → 515, and the 105 new tests are exactly the P5 suites: +18 roles, +15 handoffs, +33 orchestrator, +18 JEV, +12 CLI/route, +9 usage. (The warning is the pre-existing `VertexLLM` one.) |
| `uv run iris agents roles` | Renders the pack; researcher `cheap/escalate`, critic `strong/default`, both `file_read, memory_search` |
| `uv run iris agents handoffs` (empty workspace) | `no agent decisions in the recent traces` — exit 0, and says *why* nothing is there |
| **Live JEV** effort, multi-part question | `multi_part=0.960` → `fan_out=True` |
| **Live JEV** effort, "What is my cat called?" | `multi_part=0.040` → `fan_out=False` |
| **Live JEV** sufficiency, draft asserting two facts with `(no findings)` | `grounded=0.020`, `verdict=unsupported`, `below_gate=True` |
| **Live JEV** sufficiency, draft asserting one fact with its source line | `grounded=0.920`, `verdict=supported`, `below_gate=False` |
| Live JEV totals | 4 requests, 0 failures, `jev-latest` |

The live numbers are the point: both gates are a single 0.60 with a wide margin
on each side. These judgments are *separated*, not finely tuned — which is what
makes them safe to act on.

## DoD checklist (owner must tick)

- [x] Roles are declared data with tool allowlists that can only narrow, and an
      unknown tool name fails validation
- [x] Every delegation is a typed handoff carrying provenance, and an unsourced
      claim cannot be presented as fact (it is rendered marked, and counts are
      visible in the tool result, the trace and the API)
- [x] Routing stays tool-initiated (no regex auto-research regression); code
      enforces calls, deadline, rounds and output caps
- [x] Both JEV judgments fail open to the deterministic path
- [x] A budget breach degrades the answer, never fails the turn
- [x] `iris agents` and `GET /agents` show which agent decided what
- [x] `deep_dive` keeps working; the existing subagent tests pass unchanged
- [ ] Owner confirmation

## Deviations, and why

1. **The critic's machine-readable verdict comes from JEV, not from parsing the
   critic's prose.** A `supported | unsupported | partial` label scraped out of
   free text is a fragile interface, and the project's rule is that the judgment
   layer supplies judgments. The critic's prose rides back as the report's claim
   for the lead and the trace; the *verdict* comes from `judge_sufficiency`.
2. **`ResearchSubagent` was kept as a thin adapter** at `iris_ai.agent.subagents`
   rather than deleted. The generic `RoleRunner` is the single implementation; the
   class is a name for one configuration of it. Deleting it would have forced
   edits to `engine.py` and to a passing test file for no behavioural gain.
3. **Per-turn token attribution is exact for the turn, approximate per handoff.**
   Concurrent fan-out runs overlap, so a single handoff's token delta can include
   a sibling's usage. The turn total (`turnlog.usage_total()`) is exact. Stated in
   the runner's docstring rather than papered over.
4. **Sufficiency runs before the critic, not after.** The spec describes the
   critic producing the critique and the judgment gating the revision; running the
   judgment first makes a confidently-grounded draft cost **one request instead of
   a whole subagent run**, and the critic then runs exactly where its detail is
   worth paying for. Same policy, less spend.
5. **Fan-out collapses to one call when JEV is unavailable.** "Fail open" means
   the *pre-P5* behaviour, and pre-P5 had no fan-out. The alternative reading
   (no judgment ⇒ fan out freely) would make an unkeyed install the most
   expensive configuration, which is backwards.
6. **A second, pre-existing command-set assertion** in `tests/test_skills_cli.py`
   was updated for `agents`. It duplicates the canonical one in `tests/test_cli.py`;
   the duplication was there before this phase and is noted rather than
   restructured mid-phase.

## Findings (bugs caught by the tests, both real)

1. **`trace_summary()` emitted a `kind` key** which collides with
   `turnlog.record(kind, **fields)`. The `TypeError` is raised while *binding* the
   call — before `turnlog.record`'s own suppression runs — so it broke the shipped
   worker. Renamed to `handoff_kind`, the call site is now guarded too, and a test
   asserts the key cannot come back.
2. **The budget was per `Orchestrator` instance, not per turn.** With a cap of 2,
   the first turn of the process would have spent the allowance and every later
   turn would have been refused. Now keyed on `turnlog.active_id()`, which every
   turn's entry point already establishes — so no graph edits were needed.
3. **A trace entry could have carried a whole report**: `TurnLog.add` truncates
   top-level strings but lets nested structures through. `trace_summary()` is
   scalars only.
4. **The `TurnLog` field list was briefly clobbered** by a bad apply during Task 6
   (the accumulator landed where `judgments`/`stages`/`dropped` were). Caught by
   the usage suite within one run and repaired.

## Notes and follow-ups

- **`multi_agent_max_output_chars * multi_agent_max_calls`** is the merge cap. It
  is derived rather than a second knob so the two cannot disagree.
- **Per-claim attribution is coarse.** A report is one claim whose sources are the
  hits that were actually retrieved during the run. Finer attribution needs a
  structured output mode for roles — worth doing when a role other than the
  researcher returns findings.
- **Deferred:** structured/JSON role output; more roles; a file-defined role
  format (roles grant tools and write prompts, so they stay in code for now);
  parallel fan-out beyond `multi_agent_max_parallel`; and the `hybrid_top_k`
  cleanup, which is done (see `p4-execution.md`).
- **Still CI-only:** the five `test_memory_pipeline.py` tests (Postgres).
