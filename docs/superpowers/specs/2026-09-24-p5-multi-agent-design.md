# P5 — Multi-agent: roles, handoffs and an orchestrator policy

**Phase:** P5 of 8 ([`docs/blueprint.md`](../../blueprint.md))
**Predecessor:** P4 (skill registry, manifests, execution boundary) — implementation
complete and verified; the owner's DoD tick is still pending and was explicitly
waived for P5 on 2026-09-24.
**Status:** proposed — awaiting the owner ratifying the role pack.

## Goal

Iris stops being one loop with one hidden helper. The worker that already exists
(`ResearchSubagent`, behind the `deep_dive` tool) becomes a **role** in a small
pack of declared roles; a **critic** joins it; every exchange between them is a
typed **handoff** with provenance the owner can see. Code owns the shape —
which roles exist, what each may touch, how many times, for how long — and JEV
supplies the two judgments where a judgment beats a heuristic. Multi-agent
remains *opt-in per turn* and always falls back to the single-agent path.

## Owner decisions (2026-09-24)

| Question | Answer | Consequence |
|---|---|---|
| Start P5? | **Yes, now** — P4's DoD stays unticked while it waits | The blueprint's gate is waived by the owner, not ignored: `p5-execution.md` records the waiver and P4's checklist is untouched. |
| Role pack | **"Research the best pack for our project"** | Delegated to this spec; the pack is argued from the industry sources below and is the one item to ratify before code. |
| Routing | **Code policy + JEV judgment** | Every threshold, cap and action lives in code. JEV answers exactly two questions (below) and fails open. |
| Observability | **turnlog/trace + CLI + API** | Handoffs are in the per-turn judgment block (so `GET /traces` already shows them), plus `iris agents` and `GET /agents`. |

## Industry basis (why this shape)

- **Orchestrator-worker is the pattern that works**
  (<https://www.anthropic.com/engineering/multi-agent-research-system>). A lead
  agent plans and delegates to subagents that act as *intelligent filters*; each
  carries its own context, tools and trajectory, which reduces path dependency —
  "separation of concerns". Their internal eval put Orchestrator + subagents
  **90.2% above single-agent** on breadth-first research, and parallel tool
  calling cut research time by up to **90%**.
- **The same source is the strongest argument for restraint.** Multi-agent
  systems use about **15× the tokens of a chat** (plain agents: ~4×), and token
  usage alone explained 80% of performance variance. It is a poor fit when every
  agent must share one context, and when there are many dependencies between
  agents. Iris's ordinary turn is exactly that shape: one owner, one
  conversation, a handful of dependent tool calls. So multi-agent here must be
  **opt-in, budgeted, and single-author**, not the default path.
- **A critic must not be a mirror.** "Large Language Models Cannot Self-Correct
  Reasoning Yet" (Huang et al., ICLR 2024, ~1.4k citations) found that without
  *external* feedback, self-correction frequently fails or makes answers worse;
  LLM evaluators also show **self-preference bias**, scoring their own output
  above equivalent work by others (NeurIPS 2024). The fixes in the literature
  are the ones this spec adopts: **break correlation** (the critic runs on a
  different model/tier than the producer), **ground the critique in evidence**
  (CRITIC-style tool-interactive critiquing — the critic must point at the
  source it checked), and keep the loop **bounded** (one revision, then say so).
- **Delegation quality is a prompt problem, not an architecture problem.** The
  named early failures were spawning dozens of subagents for simple queries,
  vague task descriptions, and duplicated work. Hence: explicit role
  descriptions, a hard per-turn call cap, and a code-owned effort ladder.

## The role pack

**Lead** — the existing main agent. It *is* the orchestrator and the executor:
it holds the full tool surface (writes, tasks, Telegram, skills) and it authors
the final answer. Specialists report to it; none of them is a second author.

**Researcher** — generalizes today's `ResearchSubagent`: cheap tier, bounded
tool rounds, and a *read-only* toolset (escalation-lane `memory_search` +
sandbox `file_read`). It exists to dig where the lead would otherwise spend
strong-tier tokens.

**Critic** — new. Checks a draft answer against the findings the researcher
returned and the record itself. Read-only tools, no mutation, no delivery. It
runs on the **opposite tier** from the producer when one is available, and its
output is a per-claim verdict with a source, never a prose opinion.

That is the whole pack. The roles deliberately **not** added:

| Rejected role | Why not |
|---|---|
| **Executor** subagent | The lead already executes, with the complete tool surface and the P4 skill policy wrapping it. A second executor duplicates the main loop and adds exactly the inter-agent dependency the industry source warns about. |
| **Memory curator / librarian** | Iris already consolidates memory in `capture`, `dreaming`, `reflection` and `skill_write`. A curator agent would be a fourth implementation of one concept. |
| **Planner** | The lead's ReAct loop plus `deep_dive` is the planner. A separate planner role adds a model call to every turn to restate what the lead already decided. |
| **Citation agent** (as Anthropic has) | Not a role here — promoted to a *protocol rule* instead: every claim in a handoff must carry provenance or be marked unsourced (`merge`, below). A rule is cheaper than an agent and cannot be skipped. |

## Data model

`src/iris_ai/agents/roles.py` declares roles as data; `src/iris_ai/agents/handoff.py`
defines the wire format between them.

### `Role`

| Field | Meaning |
|---|---|
| `name` | `researcher` \| `critic` |
| `description` | Injected into the lead's prompt so delegation is specific |
| `tier` | `"cheap"` \| `"strong"` — which model tier the role runs on |
| `system_prompt` | The role's instructions |
| `tools` | **Allowlist** over the registered tool surface. A role can only ever see these; unknown names are a startup error |
| `max_tool_rounds` | Per-invocation bound (the researcher keeps today's 3) |
| `max_output_chars` | Cap on what the role may return to the lead |

A role can only **narrow** the tool surface — the same rule P4 applies to a
skill's `allowed-tools`. Two allowlists intersect; neither can add.

### `Handoff`

| Field | Meaning |
|---|---|
| `id`, `from_role`, `to_role` | Who asked whom |
| `kind` | `request` \| `report` \| `critique` |
| `question` | The delegating instruction, in the lead's own words |
| `claims` | Findings, each `{text, sources: [{path, chunk_index}], unsourced: bool}` |
| `verdict` | Critic only: `supported` \| `unsupported` \| `partial`, with its score and gate |
| `spend` | `{tool_rounds, ms, tokens}` — the tokens come from the new turn-scoped usage accumulator |
| `refused` | Set when policy or budget stopped the handoff; the reason is a stable string |

Handoff payloads are **data, never instructions**. A subagent may be reading
untrusted ingested text; when its claims come back they are formatted as
findings, and the existing JEV guard stays the thing that screens untrusted
content (P3/P4). Findings never land in a system prompt.

## Orchestration policy (code-owned)

**Routing is tool-initiated, not regex-triggered.** v2 deliberately removed
regex auto-research: the lead decides whether to call `deep_dive`, and P5 keeps
that. What P5 adds is that calling it no longer reaches a hardcoded subgraph — it
reaches the role policy, which enforces the shape.

**Code owns:** which roles exist; each role's tool allowlist, tier, round cap and
output cap; per-turn `max_calls`; the wall-clock deadline; the merge order;
whether a revision is allowed; and what happens at every gate.

**Fan-out** is available but capped: the lead may hand the researcher up to
`multi_agent_max_parallel` sub-questions, which run concurrently (this is where
the documented ~90% latency win comes from) and are merged in a stable order.
Sequential handoffs remain the default.

**Merge is deterministic.** Findings are concatenated in role order with their
provenance, size-capped, and handed to the lead — which writes the answer. There
is no merge model call: the lead is the single author, which keeps answers
coherent and costs nothing extra.

### The two JEV judgments

| Judgment | Question asked | Code's action |
|---|---|---|
| **Effort** | "Is this actually multi-part, or one question?" | Below `multi_agent_effort_gate` → do not fan out; one researcher call at most. Directly targets the documented "spawned 50 subagents for a simple query" failure. |
| **Sufficiency** | "Is every factual claim in this draft supported by the findings?" | Below `multi_agent_critique_gate` → **one** revision pass; still below → the lead must say what it could not ground instead of asserting it. |

Both are best-effort and **fail open** to today's behaviour (no fan-out, no
critique) when JEV is disabled or the key is unset — the same contract as every
other JEV integration in Iris. Code keeps every threshold: JEV supplies a
probability, code compares it and chooses the action.

## Budgets and fallback

| Budget | Default | Behaviour on breach |
|---|---|---|
| Calls per turn | 2 | Further handoffs refuse with `budget_exhausted` |
| Wall-clock per turn | 20 s | Stop launching; merge what exists |
| Tool rounds per role | 3 (researcher), 2 (critic) | Role returns its partial report |
| Output chars per role | 4000 | Truncated, marked `truncated: true` |

A breach never fails a turn: Iris answers from whatever *is* grounded, and the
trace records `budget_exhausted` with the stage it happened in. This is the
"fallback to the single-agent path" the blueprint asks for, expressed as: the
lead always ends up holding the pen.

## Observability

- **turnlog** — `record("handoff", ...)` per exchange and
  `record("agent_verdict", ...)` per critique, so "which agent decided what"
  appears in the existing judgment block with no new plumbing. Subagent runs
  inherit the turn's `ContextVar`, so their telemetry lands in the parent turn.
- **Turn-scoped usage accumulator** — small addition to `LLMClient._record`,
  mirroring `turnlog`: while a turn is active, record prompt/completion tokens
  per tier. This is what makes the 15× cost factor *measurable per turn* rather
  than a surprise in the ledger, and it fills `spend.tokens` on each handoff.
- **CLI** — `iris agents roles | show <name> | handoffs`, read-only, same shape
  as `iris skills`. `handoffs` reads the trace store that `GET /traces` serves.
  `tests/test_cli.py:28` asserts the exact command set and is updated (the same
  change P4 made when it added `skills`).
- **API** — `GET /agents`: the role pack plus a summary of recent handoffs.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `multi_agent_enabled` | `true` | Master switch; off = the pre-P5 single-agent path exactly |
| `multi_agent_max_calls` | `2` | Handoffs per turn |
| `multi_agent_max_parallel` | `3` | Concurrent researcher sub-questions |
| `multi_agent_deadline_ms` | `20000` | Wall-clock ceiling for the multi-agent section |
| `multi_agent_max_output_chars` | `4000` | Per-role return cap |
| `multi_agent_effort_gate` | `0.60` | JEV: fan out only above this |
| `multi_agent_critique_gate` | `0.60` | JEV: sufficiency floor before a revision |
| `multi_agent_revise_once` | `true` | Allow exactly one revision pass |

## Tests (fake-driven — no network, no key, no database)

| File | Pins |
|---|---|
| `tests/test_agent_roles.py` | Role declaration: tool allowlists narrow and never widen, unknown tool names error, caps apply |
| `tests/test_handoffs.py` | Handoff serialization, provenance, the unsourced rule, data-not-instructions formatting |
| `tests/test_orchestrator.py` | Routing stays tool-initiated; budgets refuse and degrade; deadline; deterministic merge order; JEV gates fail open |
| `tests/test_agents_cli.py` | `iris agents` output and exit codes |
| `tests/test_cli.py` | Command set updated to include `agents` |
| `tests/test_subagents.py` | **Unchanged behaviour**: `deep_dive` still works and still does not auto-run |

## Risks accepted

1. **Cost.** Multi-agent is ~15× a chat by the industry measurement. Mitigated by
   a 2-call default cap, the deadline, the effort gate, and per-turn token
   visibility.
2. **Correlated error.** A critic on the same model as the producer mostly
   agrees with it. Mitigated by opposite-tier placement and evidence-grounding.
3. **Latency.** Bounded by the deadline; breaches degrade rather than hang.
4. **Injection through findings.** Subagents read untrusted content and report
   back. Mitigated by the handoff being data, the guard staying where it is, and
   findings never entering a system prompt.

## Out of scope (reject during review)

Cross-org / third-party agents and a durable workflow engine (blueprint
non-goals), plus: roles defined in files (they grant tools and write prompts, so
they stay in code this phase), a separate executor or planner role, autonomous
multi-agent background runs, and any widening of a role's tools beyond what the
lead itself may use.

## DoD checklist (owner must tick)

- [ ] Roles are declared data with tool allowlists that can only narrow, and an
      unknown tool name fails validation
- [ ] Every delegation is a typed handoff carrying provenance, and an unsourced
      claim can never be presented as fact
- [ ] Routing stays tool-initiated (no regex auto-research regression); code
      enforces calls, deadline, rounds and output caps
- [ ] Both JEV judgments fail open to the deterministic path
- [ ] A budget breach degrades the answer, never fails the turn
- [ ] `iris agents` and `GET /agents` show which agent decided what
- [ ] `deep_dive` keeps working; the existing subagent tests pass unchanged
- [ ] Owner confirmation
