# JEV — TypeSafe System One, integrated

> **Status:** integrated behind a feature flag, with a deterministic fallback on
> every call site. Iris runs unchanged with `TYPESAFE_API_KEY` unset.
> **Primary sources:** <https://docs.typesafe.ai> and the TypeSafe SDK
> (`typesafe-sdk==0.7.0`, pinned in `pyproject.toml`). Everything below was read
> from the live docs, not recalled.

---

## 1. What JEV is

**Jev** is TypeSafe's flagship **System One** model. It is not a chat model: it
takes a **state** (a string, a JSON object, or an array of text) plus one or
more **typed questions**, and returns **typed answers with calibrated
probabilities**. It does not generate prose, code, or explanations.

| Primitive | Question shape | Answer |
|---|---|---|
| **Noul** | "is this true?" | `noul` ∈ [0, 1] — the probability of *yes*. No separate confidence. |
| **Choice** | "which of these options?" | `choice` + `probabilities` (per option) + `confidence` |
| **Score** | "where on this ordered scale?" | `score` (can sit between levels) + `legend` + `probabilities` + `confidence` |

Properties that make it usable as a *programming primitive* rather than a
prompt:

- **Answers are constrained to the options you supplied.** No parsing prose, no
  "the model wrapped JSON in markdown" recovery path.
- **Every question is independent and evaluated in parallel**, and asking extra
  questions costs only their tokens — TypeSafe's own parallel-questions
  cookbook measures one 13-question request at **12.2× cheaper and 10.0× faster
  than thirteen separate requests**. This is what makes one-request reranking
  viable.
- **`confidence` is derived from the distribution shape**, so code can decide
  *when to act* as well as *what to do*.
- **Code owns every threshold and action.** The docs are explicit: keep rules,
  calculations, lookups and execution in code.

Pricing and limits (`jev-1.13.0`, alias `jev-latest`):

| | |
|---|---|
| Price | **$42 per billion input tokens** ($0.042/Mtok). Output tokens are free. |
| Context | 64k tokens/request; 32k for `state` plus the longest question |
| Rate limits | 250k tokens/sec, 1,200 requests/min (currently adjusting dynamically) |
| Input | **Text only** — no image, audio or video |
| Languages | English primary; other languages supported but weaker |

## 2. Why Iris uses it

Iris's memory decisions were made by hand-tuned arithmetic and substring tests.
Those are proxies for judgments a System One model makes directly, and two of
them were measurably wrong:

1. **Recall relevance.** The default lane fused `0.6·vector + 0.4·FTS`, then
   multiplied by decay × importance. `reports/eval_lab.md` recorded the full
   pipeline scoring **0.83 recall@5 against 1.00 for plain cosine** — the fusion
   term was making ranking *worse*.
2. **Skill selection.** `match_triggers` was a casefolded substring test against
   the trigger list. A skill only surfaced if the owner's exact wording happened
   to contain a trigger phrase, and a short trigger could fire inside an
   unrelated word.
3. **Injection defence.** Untrusted content was (and is) structurally barred
   from curated memory, but nothing stopped the model from *following* an
   instruction embedded in a retrieved page; the only defence was a prose
   banner.

JEV addresses all three with one request each, at roughly $0.00004–0.0004 per
call.

## 3. Where it is integrated

```
                    ┌─────────────────────────────┐
  user turn ───────►│ ContextAssembler            │──► JEV #1  skill suggestion
                    │  (static tiers + skills)    │     1 Choice + 1 Noul
                    └──────────────┬──────────────┘
                                   ▼
                    ┌─────────────────────────────┐
                    │ agent ReAct loop            │
                    │  memory_search ─────────────┼──► JEV #2  recall rerank
                    │  web_search / ingest_url ───┼──► JEV #3  injection screen
                    └─────────────────────────────┘
```

| # | Integration | Code | JEV primitives | Deterministic fallback |
|---|---|---|---|---|
| 1 | **Recall reranking** | `src/iris/jev/recall.py`, called from `MemoryIndex._rerank` | one **Noul** per candidate, all in one request | the `0.6·vector + 0.4·FTS` score |
| 2 | **Skill suggestion** | `src/iris/jev/skills.py`, called from `ContextAssembler._skills_block` | **Choice** over the roster + a "needs a skill at all?" **Noul**, in one request | `SkillLibrary.match_triggers` |
| 3 | **Injection screening** | `src/iris/jev/guard.py`, called from the `web_search` and `ingest_url` tools | 2 **Nouls** (injection, exfiltration) + 1 **Score** (severity) per item, batched into one request | plain `[UNTRUSTED]` tagging only |

### 3.1 Recall reranking — the composition rule

JEV replaces the **relevance** term *only*:

```python
hit.relevance = (1 - blend) * noul + blend * hit.relevance   # blend default 0.15
hit.score     = hit.relevance * hit.decay * hit.imp_mult
```

Recency decay and importance stay deterministic, because they encode **product
policy** — what Iris is allowed to forget — and policy must not be outsourced to
a model. This is the composite-scoring pattern: one model judgment, weights and
thresholds in code. `MemoryHit` carries `relevance`/`decay`/`imp_mult` so the
arithmetic is inspectable and testable without a model
(`tests/test_jev.py::test_rerank_preserves_policy_multipliers`).

Two lanes opt out by design:

- `ablation={"vector_only"}` — the pure-cosine baseline must never consult JEV,
  or the ablation stops measuring anything.
- `ablation={"no_rerank"}` — JEV off, for A/B measurement.

### 3.2 Skill suggestion

One request: `Choice` over `{skill_name: "description · triggers: ..."}` with an
explicit `none_of_these` option, plus `Noul("does this request need a stored
procedure at all?")`. A name is suggested only if the choice is not
`none_of_these`, `needs_skill ≥ JEV_SKILL_GATE` (0.30), `confidence ≥
JEV_SKILL_MIN_CONFIDENCE` (0.30), **and** the name is in the roster that was
sent — a hallucinated name can never surface.

### 3.3 Injection screening

Per item: two Nouls and a severity Score, batched. Thresholds are a readable
policy in `config.py`:

```python
hazard = max(injection, exfiltration)
if hazard >= JEV_GUARD_BLOCK_THRESHOLD (0.70):   BLOCK
elif hazard >= JEV_GUARD_REVIEW_THRESHOLD (0.35):
    BLOCK if severity >= JEV_GUARD_SEVERITY_BLOCK (2.0) else REVIEW
else: PASS
```

- `PASS` → stored/indexed as ordinary untrusted data.
- `REVIEW` → ingested, but the tool result and the stored import carry a
  `[UNTRUSTED — SCREENED SUSPICIOUS: …]` banner.
- `BLOCK` → `ingest_url` refuses before the write (a hostile page never becomes
  recallable), and `web_search` withholds the body and returns `"trust": "blocked"`.

**Failure policy: fail open, never fail closed.** If JEV is unset or down, the
verdict is PASS-with-`screened=False`, which is exactly the pre-JEV behaviour.
Untrusted content stays untrusted either way — screening is an additional gate,
never the trust boundary.

## 4. Where JEV is deliberately **not** used

| Not used | Why |
|---|---|
| Provenance / promotion gating, decay math, supersession, SQL, hashing, sandboxes | Deterministic guarantees. A probabilistic model must not sit inside a security or integrity boundary. |
| Reply generation, persona, voice | Jev produces no text. |
| Compaction summaries, REM consolidation statements, contextual chunk headers, onboarding prose | These need *generation*. The cheap-tier LLM stays. |
| Embeddings / vector search | Jev has no embedding endpoint. |
| Images, voice notes | Text-only input. |
| Evaluating its own output as ground truth | Typed output guarantees the interface, not truth. Calibration is measured across group predictions, not per answer. |

## 5. Configuration

```dotenv
TYPESAFE_API_KEY=            # console.typesafe.ai — empty = JEV disabled
JEV_ENABLED=true
JEV_MODEL=jev-latest         # alias → jev-1.13.0; pin the version to freeze behaviour
JEV_TIMEOUT_SECONDS=12.0
JEV_RERANK_ENABLED=true
JEV_RERANK_CANDIDATES=20     # shortlist head that gets reranked (one request)
JEV_RERANK_BLEND=0.15        # weight kept for the deterministic hybrid score
JEV_SKILL_GATE=0.30
JEV_SKILL_MIN_CONFIDENCE=0.30
JEV_GUARD_ENABLED=true
JEV_GUARD_BLOCK_THRESHOLD=0.70
JEV_GUARD_REVIEW_THRESHOLD=0.35
```

`JEV_DISABLED_REASON` exists so tooling (the eval lab) can force the
deterministic path without unsetting the key.

## 6. Examples

One recall rerank (20 candidates, one request, ~8k input tokens ≈ $0.0003):

```python
from iris.jev import JevClient, JevReranker

jev = JevClient(ledger=ledger)
scores = await JevReranker(jev).relevance(
    "when does my lease renew?",
    ["I bought new headphones", "the lease renews in September", ...],
)
# -> [0.04, 0.96, ...]   len == head that was scored
```

Screening a fetched page:

```python
from iris.jev import screen_untrusted

verdict = await screen_untrusted(jev, page_text, source=url)
if verdict.action is GuardAction.BLOCK:
    raise ValueError(f"refusing to ingest: {verdict.reason}")
stored_banner = verdict.banner()
```

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `jev disabled (TYPESAFE_API_KEY is not set)` at boot | No key | Expected — every integration falls back. Set the key to enable. |
| `jev disabled (typesafe-sdk is not importable …)` | SDK missing from the environment | `uv sync` |
| `jev request failed (…TypeSafeRateLimitError)` in logs | 429 | The SDK retries with backoff and honours `Retry-After`; the caller falls back to the deterministic path meanwhile. Lower `JEV_RERANK_CANDIDATES` to cut tokens/request. |
| Recall ordering looks unchanged | `JEV_RERANK_ENABLED=false`, no key, or an ablation is active | Check `/health` (`"jev": true`) and the boot log line. |
| Everything scored `PASS` and `screened: false` | Screening disabled/unavailable | By design (fail open). |
| `confidence` below threshold on every skill suggestion | The roster has no matching procedure | Correct behaviour — it refuses to guess. |

Verify the integration is live:

```bash
uv run python -c "from iris.jev import JevClient; c=JevClient(); print(c.enabled, c.unavailable_reason())"
uv run pytest tests/test_jev.py -q      # 22 tests, no key, no network
curl -s localhost:8000/jev -H "Authorization: Bearer $IRIS_API_TOKEN"   # health, counters, last error
```

## 8. Observability

Three things make the layer checkable rather than assumed:

- **`GET /jev`** (authenticated) reports `enabled`, the reason it is off when
  it is, and `requests` / `failures` / `last_latency_ms` / `last_error`.
  `/health` carries the same block as `judgment` (plus `/health`'s `jev` bool).
  A layer that is silently falling back looks identical to a healthy one
  without these counters.
- **Every decision lands in the turn trace.** `config/traces.jsonl` gains an
  `events` list and `stages_ms`: each rerank records the probability given to
  each of its top candidates, each guard verdict records `action` and its
  injection/exfiltration/severity scores — **including the items that passed,
  and including `screened: false` when nothing was checked** — and each skill
  decision records its gate inputs. Recording is bounded per turn and cannot
  raise; see `src/iris/turnlog.py`.
- **One `asyncio.Lock` guards client construction.** A single turn can issue a
  rerank (inside a recall tool), a guard screen (inside `web_search`) and a
  capture judgment, and reflection now runs in the background — without the
  lock two concurrent callers each build a client and leak one.

## 9. Tests

`tests/test_jev.py` (17 tests) and the audit suite pin the whole adapter
contract without a key or a network: normalization of SDK answers, ledger
accounting at `tier="jev"`, one-request batching, the candidate cap, multiplier
preservation, ablation opt-out, guard routing and banners, fail-open behaviour,
skill gating, hallucinated-name rejection, assembler preference and fallback,
and the two tool-level wirings (search tagging, ingest refusal).
