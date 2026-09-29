# 07 — Observability, judgments & evaluation

Iris is already unusually observable for its size (per-turn traces with stage
timings, judgment events, a cost ledger, an eval lab with intervals). The
redesign keeps that and adds a standard wire format and a cleaner separation.

## 1. Trace model (OpenTelemetry GenAI, researched)

The 2026 consensus is OTel GenAI semantic conventions: **an agent run is a trace,
a tool call is a span, a model call is a span, token usage is a metric.** Iris
adopts that mapping:

| Iris event | OTel |
|---|---|
| one turn | a trace (`gen_ai.operation.name = invoke_agent`) |
| a model call | a client span (`gen_ai.*` model/usage attributes) |
| a tool call | an internal span (`execute_tool`, tool name + call id) |
| a RAG retrieval | a retriever span |
| tokens/cost | metrics |
| guard refusal / approval | events on the turn span |

The conventions are still "Development" status, so the exporter is optional and
versioned; the **local JSONL trace remains the default** so startup needs no
collector. A user points it at OTLP when they want a backend.

## 2. Judgments (kept, optional, first-class)

TypeSafe/JEV supplies typed judgments (reranking, skill selection, injection
screening, capture, sufficiency) with deterministic fallbacks. The redesign
generalizes the shape to a `Judge` capability:

- a judgment is a *typed question over supplied text*, returns a probability/
  label, is batched where possible, and **always has a deterministic fallback**;
- every judgment is recorded on the turn trace (what was asked, what won, how
  long) — "not checked" recorded as explicitly as "checked";
- the built-in implementation is JEV; a local model judge or a rules judge can be
  registered instead.

A judgment that needs *generation* is not a judgment and stays with the model.

## 3. Cost & budgets

Kept: every model call appended to a ledger with usage + estimated cost, cache-hit
rate rollups. Budgets are kernel-enforced and split by kind (input/output/cached/
embedding/tool-schema), with a per-turn and a per-day ceiling that survives a
restart. `iris costs`, `iris budgets` read them.

## 4. Evaluation

Kept, because it is rare and valuable:

- Wilson intervals for rates, seeded bootstrap for means, **paired** intervals
  for candidate-vs-baseline, a measured noise floor, and a pre-registered
  decision rule that reports `inconclusive` rather than passing when it cannot;
- a model-free retrieval gate in CI (recall@k / nDCG@k) so a ranking regression
  fails a PR;
- the ablation lab including a memory-OFF baseline.

The redesign adds: **recovery tests** (kill mid-turn, assert exactly-once
completion), a **message-contract conformance test**, and a plugin contract test
per kind — the classes of bug that actually shipped.

## 5. Logging

Structured logs with a level knob (the LiteLLM warning flood was a real symptom).
The rule from today holds: **never log a secret value**; tool arguments are
hashed; debug re-raises instead of hiding a traceback behind a friendly line.

## 6. Health

`GET /health` (liveness + judgment summary + in-flight background work),
`GET /jev` (enabled? why not? counters), `GET /guards` (live circuit state).
`iris doctor` stays offline and CI-safe; the model health probe from `04` runs
only when asked, so doctor never needs network.
