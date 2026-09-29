# Observability — traces, spans and spend

Iris records what a turn did in three places, and they are deliberately
different things rather than three copies of one:

| Layer | What it is | Where it lives | Cost when unused |
|---|---|---|---|
| **Turn traces** | the local, always-on record: which prompt version ran, which tools were called, how each stage spent its time, the judgment that was made | `workspace/config/traces.jsonl` (`GET /traces`, `iris agents handoffs`) | ~nothing; a JSONL append off the reply path |
| **Spans** | the *same* events shaped for the GenAI semantic conventions, so an operator's existing backend shows Iris turns and tool calls as first-class spans | OTLP, only when asked | zero: the SDK is an optional extra |
| **Cost ledger** | every model call: model, tokens, cached tokens, price | `workspace/config/llm_calls.jsonl` (`iris costs`) | one append per call |

The rule that keeps this a feature instead of a tax: **tracing is never on the
critical path.** The traces and the ledger are written after the reply is
produced, and telemetry is a *subscriber* to the hook bus rather than a branch in
the agent loop.

## Spans

```bash
pip install "iris-personal-ai[otel]"
```

```bash
OTEL_EXPORTER=otlp
OTEL_ENDPOINT=http://127.0.0.1:4318/v1/traces   # empty ⇒ OTEL_EXPORTER_OTLP_ENDPOINT / the SDK default
AGENT_NAME=iris                                  # becomes gen_ai.agent.name
```

`OTEL_EXPORTER=none` (the default) keeps a local single-user harness free of a
telemetry stack. `otlp` without the extra is a **boot error naming the extra** —
never a quiet no-op, because an operator staring at an empty dashboard with no
error to read is the worst version of this feature.

### What is emitted

| Span | Attributes |
|---|---|
| `turn` (one per `turn_end`) | `gen_ai.operation.name=invoke_agent`, `gen_ai.agent.name`, `iris.turn.duration_ms`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, `gen_ai.request.model` when known, `error.type` on failure |
| `<tool name>` (one per `pre_tool`/`post_tool` pair) | `gen_ai.operation.name=execute_tool`, `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.tool.call.id`, `iris.tool.side_effecting`, `iris.tool.digest`, `error.type` on failure |

**Tool arguments are a digest, never an attribute.** A credential in a tool
argument must not become a span attribute in someone's SaaS backend — the same
rule the traces follow, for the same reason.

Two guards keep the shapes honest rather than merely intended:

- `iris_ai.observability.spans.GENAI_ATTRIBUTES` is the complete set of keys this
  project will ever emit, and `tests/test_otel_spans.py::test_every_span_stays_on_convention`
  asserts every span's attributes are a subset.
- `SpanSink.emit` drops any stray key with a warning. OpenTelemetry does not error
  on an undefined attribute name — it arrives at the backend as *nothing* — so a
  typo like `gen_ai.usage.prompt_tokens` would otherwise silently vanish.

### Where the whole thing is optional

`iris_ai.observability.spans` is pure: it shapes spans from events the harness
already has, with no SDK, no collector and no network. That is why the shaping,
the convention guard and the refusal are all unit-tested in an environment with
no telemetry installed, and why the send path is a thin `SpanSink` on top.

## Traces and the ledger

The traces are local by design: a single-user harness should be able to answer
"what did the last turn do" without a service. They are read through
`GET /traces` and, for the multi-agent decisions inside them, `iris agents
handoffs`. The ledger has its own terminal reader:

```
iris costs            # totals, and a by-model table
iris costs daily      # the last 14 days (`-n 30` for a different window)
iris costs weekly     # the last 4 weeks
```

Two honesty rules, carried from the ledger itself:

- a model with **no price** in the table is *named* (`unpriced_models`), so a
  total is never quietly lower than reality;
- an empty ledger says so rather than printing a confident `$0.00`.

## The eval gate

Retrieval quality is measured, not asserted: `tests/test_retrieval_gate.py`
scores a labelled fixture with `recall@k` and `nDCG@k` against a real pgvector
index with no model in the loop, and CI runs it as its own job (`retrieval`) so a
ranking regression names itself in the checks list instead of hiding inside a
900-test run. See `docs/redesign/07-observability-eval.md` for what the numbers
mean and what is deliberately not gated yet.
