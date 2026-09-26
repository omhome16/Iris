# Vault review — Iris against the AI-Mastery notes

**Source:** `AI-Mastery` (a sibling learning vault: 112 notes across foundations,
transformers, app engineering, RAG, memory/agents, protocols, eval/observability/
security, inference, and infra).
**Method:** the note *structure* was read in full (Home, Vault Conventions,
Learning Paths, Deep Reference Index, Diagram Pack Index, Labs Catalog, Cheat
Sheets, Glossary, the Concept-Note template and every domain MOC), the provider
note end to end, and then the `🏭 Production reality` and `💥 Failure modes`
sections of **every** note in the domains Iris actually implements (`03`, `04`,
`05`, `06`, `07`, `09`). Those two sections carry the actionable content; the
rest is explanation aimed at a human learner.

**Why this file exists:** so "we checked" is reviewable rather than asserted, and
so a recommendation that was deliberately *not* adopted is recorded with its
reason instead of being quietly dropped.

---

## Where Iris already meets the bar

These recur across the vault, and Iris implements them — several are asserted by
tests, which is why they are marked *enforced* rather than *present*.

| Vault recommendation | Iris | Evidence |
|---|---|---|
| Guard order `budget → circuit → spiral/dedup → context → record` | **exact match** | `src/iris_ai/guards.py`; the dotted chain is in its module docstring |
| Enforcement must be **pre-dispatch**, not observability | **enforced** | `GuardChain.before()` refuses before the tool runs; every refusal lands in the trace as `tool_guard` |
| Spiral detection: same tool + near-identical args ≥3×, Jaccard > 0.72 | **enforced** | `tests/test_guards.py` |
| Two consecutive failures open a circuit; three failing tools escalate | **enforced** | `tests/test_guards.py` |
| Budgets split by kind (input/output/cached/embedding/tool-schema) | **enforced** | `src/iris_ai/budget.py`, `tests/test_budget.py` |
| Approvals bound to the **post-edit** argument digest, fail-closed | **enforced** | `src/iris_ai/approval.py`, `tests/test_approval.py` |
| One retry owner; jitter; `Retry-After`; classify before retrying | **enforced** | `src/iris_ai/memory/llm.py` |
| Cache-first prompt: stable prefix first, volatile last | **enforced** | stable-prefix context assembly; hit rate in `/costs` |
| Compaction at a threshold, with a keep-budget, bounded forever | **enforced** | `tests/test_compaction.py` |
| Consolidation off the hot path | **enforced** | `src/iris_ai/background.py`, `tests/test_reflection.py` |
| Trust is structural, not a prompt rule: untrusted/system never promote | **enforced** | `Origin.promotable`, `src/iris_ai/memory/provenance.py`; `tests/test_memory_units.py` |
| Invalidate rather than delete; keep history | **enforced** | supersession keys; `tests/test_forget_route.py` |
| Screening of untrusted content for instruction injection | **enforced** | `src/iris_ai/jev/guard.py`, `tests/test_jev.py`, `tests/test_ingest.py` |
| Tool classes drive policy; a capability cannot arrive unclassified | **enforced** | `src/iris_ai/toolpolicy.py`, `tests/test_tool_policy.py` |
| Deny-by-default with visible, declared tool surface | **enforced** | `iris tools`, `TOOL_SURFACE_BUDGET` |
| Skills can only *narrow*; script execution is one gated boundary | **enforced** | `tests/test_skill_policy.py`, `tests/test_skill_runner.py` |
| Eval reports intervals and a decision rule, not point estimates | **enforced** | `src/iris_ai/eval/stats.py`, `tests/test_eval_stats.py` |
| `recall@pool` visible at runtime, not only in eval | **present** | the rerank event records `shortlist` (candidate pool) and `scored`, plus the probability given to each candidate — `src/iris_ai/memory/index.py`. The vault calls this the single most valuable debugging affordance, and it is why a thin pool is visible before anyone blames the generator |
| Prompt/model identity on every trace | **enforced** | `model` per tier via `TurnLog.models`; `PROMPT_VERSION` + an assembled-prefix fingerprint via `turnlog.note_prompt`, `tests/test_prompt_identity.py` |
| Non-root container, pinned base image, healthchecks | **present** | `Dockerfile` (`uid=10001`), `docker-compose.yml` |
| Truth in plain files, index rebuildable | **present** | Markdown soul + pgvector as a derived index |

## Adopted during this review

| Change | Vault pressure | Commit |
|---|---|---|
| Distribution → `iris-personal-ai`, import → `iris_ai` | Publishability (the vault's "supply chain treated as code execution" is the same logic: a name you cannot own is a liability) | `66505cd` |
| One provider registry; 12 providers; any OpenAI-compatible endpoint | "Thin adapter; expose provider-specific knobs explicitly rather than trying to unify them" | `90e57bc` |
| A provider with a key but no model id is skipped, and doctor says so | "Never guess an id; model names drift" — the repo's own Groq defaults had already broken once | `90e57bc` |
| Builtin skill force-included into the wheel | "If the README advertises it, the artifact must contain it" | `f22157c` |
| CI runs `iris skills list` from an installed wheel | "A build that only exists in pyproject.toml is a claim, not a deliverable" | `f22157c` |

## Deliberately not adopted (with reasons)

| Vault recommendation | Why not (yet) |
|---|---|
| MicroVM / gVisor / Wasm isolation for untrusted execution | Iris's skill scripts are **the owner's own** code behind an approval gate, not third-party code. The vault reserves kernel-boundary isolation for untrusted *third-party* execution. Process isolation with a built env, an argv (never a shell), a timeout and capped output is the correct level for this threat model — and `docs/deployment.md` states the limit plainly. |
| Full multi-agent topologies (supervisor DAGs, bidding, leases) | The vault's own numbers argue against it: multi-agent costs ~15× a chat and coordination overhead grows super-linearly. Iris runs one supervisor with two specialists and code-owned bounds, which is the shape the vault recommends for the 80% case. |
| Graph RAG | "Very few full graph deployments with demonstrated business value"; the vault says prefer light/lazy. Iris's two recall lanes plus reranking already cover the measured need, and there is no ablation showing a graph would help *this* corpus. |
| Semantic caching | "Semantic caching only where measured, narrow and version-tagged." Iris has prompt-prefix caching with hit-rate reporting; a semantic cache without a measured precision would risk serving a wrong answer. |
| A formal prompt registry service | The vault wants prompts versioned. Iris keeps prompts in-repo (already versioned by git) with the model id pinned per provider; a registry service is infrastructure this project does not yet need. |

## Backlog (accepted, not yet implemented)

Ordered by value, with the vault's own justification.

> Two items were on this list and are now resolved: **prompt/model identity on
> the trace** (implemented — `PROMPT_VERSION` plus an assembled-prefix
> fingerprint, `tests/test_prompt_identity.py`) and **`recall@pool` logged
> continuously** (found to be already present — the rerank event carries the
> candidate pool size and the score of every candidate it judged).

1. **A model-free retrieval gate in CI.** Two-speed gating: deterministic
   retrieval metrics on every PR, judge-based suites nightly. Today the retrieval
   eval is `reports/eval_lab.md`, generated by hand, and not a gate.
2. **A memory-OFF ablation baseline.** The vault: "memory was never evaluated
   with an OFF baseline so nobody noticed it hurt." Iris has an ablation study
   but it predates the JEV-era capture node.
3. **Deferred tool loading / tool search** once the surface passes ~20 tools.
   Iris declares the visible surface and reports it, but does not yet let the
   model request a subset by namespace.
4. **Hash-pinning for third-party skill packages with re-approval on change.**
   The vault's "rug pull after an update" case. Iris's `package:` source trusts
   an installed distribution; that is fine while everything is first-party.
5. **A kill switch and a best-so-far answer path** on a halted turn — the vault
   wants every agent to "degrade instead of failing".
6. **TTFT/TPOT split** rather than total latency, so p99 can be attributed.
