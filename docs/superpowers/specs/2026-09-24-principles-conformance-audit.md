# Conformance audit — Iris against the AI-Mastery principles

**Date:** 2026-09-24
**Scope:** the agent graph, memory, context, tools, budgets, observability, eval
and security of `iris`, audited against the owner's learning vault
(`D:\AI\AII\AI-Mastery`, verified `2026-09`).
**Method:** every claim below was checked against this repo's code, not inferred
from the docs. Where a finding is an *absence*, the search that failed to find it
is named. Vault sources are cited by note, so a reader can re-derive each rule.

**Vault notes used:** `MOC 05 - Memory & Agents`, `MOC 06 - Protocols &
Multi-Agent`, `MOC 07 - Eval, Observability & Security`,
`40-CHEATSHEETS/Cheat Sheets`, and the concept notes `Agent Harness`,
`Tool Design and Ecosystems`, `Agent Control, Budgets and HITL`,
`Agent Failure Engineering`, `Multi-Agent Economics`, `Observability and
Tracing`, `Evaluation Systems and CI Gates`.

---

## 1. Verdict

Iris is **aligned with the vault's core positions**, and several of its choices
are the *same* conclusions the vault reaches independently — agent-invoked
retrieval, budgets enforced pre-dispatch outside the framework, memory poisoning
defended on the write path, capability constraints instead of filters, "sample
more, reflect less", single-agent as the default. It is **not yet conformant in
six specific places**, all of which are absences rather than mistakes:

| # | Gap | Vault rule | Status |
|---|---|---|---|
| **G1** | No loop/spiral detector on the tool path | same tool + normalised args ≥3×, arg Jaccard > 0.72, >5–10 calls/turn → detect **pre-tool**; worst case is a ~200× cost blowup ($2 / 30 s vs $0.01) | **missing** |
| **G2** | No cascade breaker for repeated tool/JEV failure | 2 consecutive same-tool failures → open the circuit for the run; 3+ failing tools in a turn → escalate | **partial** (JEV client resets itself; nothing per-tool) |
| **G3** | Budgets are per-turn only | versioned policy at per-call / per-run / per-agent / **cross-session** scopes, counters split input / output / cached / tool-schema / embedding | **partial** (P5 added per-turn + tier split) |
| **G4** | Approval resume is not bound to an argument digest and has no replay guard | bind the approval to the **effective digest of arguments after edits**, guard `tool_call_id` replay, terminal-state guard, fail-closed for side-effecting tools | **missing** |
| **G5** | Traces are metadata-only by *accident*, not by policy — and nothing is redacted | 100% structural tracing, **sampled content**, redaction, short retention; span carries model **version**, prompt hash, **args hash**, cost, guardrail decision | **partial** (no `redact`/`scrub` anywhere in `src/`) |
| **G6** | Eval has no statistics and no judge calibration | measure the noise floor (repeat 3–5×), report CIs, ~63 samples/arm for Δ=0.02 at σ=0.04, frozen holdout, pre-registered decision rule, **measure judge–human agreement before trusting a judge** | **missing** (`scripts/eval_lab.py` reports point estimates only) |

Also worth recording, because they are *shapes* the vault warns about:

- **Tool surface is over the soft limit.** `TOOL_NAMES` holds **23** tools and
  every one of them is visible in an owner turn. The vault's guidance is ≤20
  visible, namespaces at 20–100, and deferred loading + tool search past 50.
  Iris is 3 over the first line — at the point selection accuracy starts to
  degrade and tool-schema tokens start to cost. (P7 will add more.)
- **Tool classes are not declared.** P5 introduced `READ_ONLY_TOOLS`, but the
  vault's model is a classification — read / external write / credentialed /
  network / filesystem / payment — that *drives* default policy per class.
- **Sandbox is process isolation, not a kernel boundary.** P4 documented this as
  accepted residual risk; the vault's position is stronger ("sandboxing must be
  at a kernel boundary, not a container, for untrusted or agent-authored code").
  Fine for owner-authored skills, **not** fine for third-party skill packs.

---

## 2. Where Iris is already conformant (with the receiving rule)

| Axis | Iris's implementation | Vault rule it satisfies |
|---|---|---|
| **When to be an agent at all** | v2 made retrieval agent-invoked; P5 keeps fan-out **off** unless a judgment says the question is genuinely multi-part | "If a single search suffices, agentic RAG is waste (3–10× tokens)"; "workflow first, escalate to agent on novelty" |
| **Single vs multi-agent** | 1 lead + 2 specialists, ≤2 handoffs, ≤3-wide fan-out; there is deliberately **no executor role** | "Most production systems are single-agent. Multi-agent is a default you must earn"; "supervisor with ≤5–8 workers" |
| **Coordination cost** | n is tiny by construction | broadcast cost 0.023n²+0.04n (50% of budget at n=7); hierarchical is **−23% accuracy** at scale |
| **Handoff discipline** | typed `Handoff`; **unsourced claims marked, never asserted**; empty report renders "no findings"; no role may delegate (no recursion); cap per turn | "Handoffs need an owner and a cap"; handoff failure split: signal corruption 37% / data gap 29% / referential drift 27% |
| **Self-critique** | the critic is **heterogeneous** (opposite tier), evidence-bound, and bounded to **one** revision | "Sample more, reflect less" — self-refine −3.6 to −10 pp without an oracle; third-party critique ≫ self-reflection |
| **Budgets** | enforced **pre-dispatch**, outside the framework; fan-out is **pre-charged**; a breach returns a stable reason and the lead still answers | "Budgets must be enforced before dispatch, outside the framework — a dashboard is not a control"; "reserve before, reconcile after" |
| **Degradation** | every JEV integration fails open to the deterministic path; a breach degrades the answer rather than failing the turn | "Every agent has a kill switch and a best-so-far answer path so halting degrades instead of failing" |
| **Prompt vs harness split** | hard ceilings, termination, secrets, filesystem limits are all structural (config, sandbox, `_child_env`), not prose in a prompt | the harness note's split table: hard cost ceiling ❌prompt/✅harness, "never delete files" ❌/✅, secrets ❌/✅ |
| **Memory poisoning** | `Origin.UNTRUSTED` can never be promoted into curated memory; the guard screens ingested text; findings are **data, never instructions** | "Memory poisoning is persistent — injection filters score 0%; defend the write path"; "instruction files say what the agent *should* do, the sandbox what it *can*" |
| **Procedural memory** | Agent Skills manifests, `allowed-tools` that can only narrow, one gated execution path | tool-security rule: "allow/deny/ask, most-specific wins, **deny always wins**"; description = instructions |
| **Context engineering** | write/select/compress/isolate are all present: bootstrap budgets, stable-prefix cache ordering, compaction at a trigger with a keep-budget, agent-invoked retrieval | the four operations; "cache-shape discipline"; "selective memory retrieval — up to 14× saving" |
| **Observability** | per-turn JSON traces with a judgment block, stage timings, per-tier token usage, cost ledger, `iris agents handoffs` + `GET /agents` | "traces as eval raw material"; "guardrail decisions recorded"; "cost attributed" |
| **Determinism where it matters** | provenance gating, decay math, supersession, hashing, the sandbox are all deterministic; JEV never sits inside an integrity boundary | "a probabilistic model must not sit inside a security or integrity boundary" |
| **Eval discipline (partial)** | ablation lanes (`vector_only`, `no_decay`, `no_importance`, `no_mmr`, `no_rerank`) isolate one term at a time | "one number is never enough"; layered, failure-localising evaluation |

### Deliberate, documented deviations (not defects)

| Iris choice | Why it is right here | Vault rule it knowingly departs from |
|---|---|---|
| Package installs are **not** approval-gated; only skill **code** and destructive memory ops are | single-owner personal assistant; an approval on every write is hostile UX, and the filesystem surface is one sandbox directory | "fail-closed defaults for side-effecting tools" (written for multi-tenant production) |
| Script isolation is **process**, not microVM/Wasm | scripts are owner-authored; the container boundary is documented as the place to put third-party skills | "kernel boundary, not a container, for untrusted code" — **accepted for now, revisit in P7** |
| No per-tenant budgets | there is exactly one tenant, the owner | "budgets are an abuse control too — per-tenant quotas" |
| No OpenTelemetry export | single-process local app with a JSONL trace; the span *fields* matter more than the wire format, and G5 addresses those | "OpenTelemetry-shaped tracing as the vendor-neutral backbone" |

---

## 3. Blueprint changes made from this audit

The audit is not a wish list: each gap became a concrete item, placed in the
phase whose subsystem owns it.

| Gap | Lands in | Item |
|---|---|---|
| G1, G2 | **P5 · harness hardening** | a pre-tool guard chain in the documented order — **budget → circuit → spiral/dedup → context → record** — with the vault's thresholds, plus a runaway simulation in CI that asserts the *refusal* |
| G3, G4 | **P5 · harness hardening** | budget policy scopes (per-turn already; add per-day/cross-session) with counters split, and an approval envelope bound to the post-edit argument digest with a replay guard |
| G5 | **P6 · cron** (scheduled runs make it load-bearing) | trace content policy: metadata by default, redaction, and the span fields the vault names (`args hash`, model version, guardrail decision, cost per span) |
| Tool surface + classes | **P7 · computer-use** | tool classification that drives default policy, and the surface strategy (namespacing / deferred loading) before P7 adds more tools |
| Sandbox | **P7 · computer-use** | decide and implement the kernel-boundary story for any non-owner-authored code |
| G6 | **P8 · ship** | eval gates with statistics: noise floor, CIs, frozen holdout, pre-registered rules, judge–human calibration, and cost/latency as first-class gate metrics |

These are additive: no shipped behaviour is being reversed, and each item has a
falsifiable acceptance test ("asserts the refusal, not the alert").
