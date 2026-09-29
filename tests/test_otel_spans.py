"""Telemetry: the span shapes are the contract, and the extra is optional.

The roadmap's gate for this half is "OTel spans validate against the semantic
conventions' schema". There is no schema file to load here, so the check is the
one that can actually fail: **every attribute a span carries is one this project
declares as convention-defined**, asserted on the spans themselves rather than on
a comment. A misspelled key does not error in OpenTelemetry — it arrives as
nothing — which is why the guard exists in code (`off_convention`) and in a test.

The send path needs the optional SDK; the shaping and the refusal do not, and that
is the split the tests follow. If the SDK is installed in this environment the
refusal test skips rather than lying about it.
"""

from __future__ import annotations

import pytest

from iris_ai.observability.otel import EXPORTERS, OtelUnavailable, SpanSink, exporter, install, sdk_available
from iris_ai.observability.spans import GENAI_ATTRIBUTES, Span, off_convention, tool_span, turn_span

# ── shaping ─────────────────────────────────────────────────────────────────


def test_a_turn_span_speaks_the_conventions():
    span = turn_span(agent="iris", duration_ms=1234.5678, usage={"prompt_tokens": 900, "completion_tokens": 120})
    assert span.name == "turn"
    assert span.attributes["gen_ai.operation.name"] == "invoke_agent"
    assert span.attributes["gen_ai.agent.name"] == "iris"
    assert span.attributes["gen_ai.usage.input_tokens"] == 900
    assert span.attributes["gen_ai.usage.output_tokens"] == 120
    assert span.attributes["iris.turn.duration_ms"] == 1234.568
    assert span.status == "ok"


def test_a_tool_span_carries_the_call_not_the_arguments():
    """A credential in a tool argument must not become a span attribute."""
    span = tool_span(
        agent="iris",
        tool="file_write",
        call_id="call-1",
        digest="abc123",
        ok=True,
        side_effecting=True,
    )
    assert span.attributes["gen_ai.operation.name"] == "execute_tool"
    assert span.attributes["gen_ai.tool.name"] == "file_write"
    assert span.attributes["gen_ai.tool.call.id"] == "call-1"
    assert span.attributes["iris.tool.digest"] == "abc123"
    assert span.attributes["iris.tool.side_effecting"] is True
    assert "content" not in span.attributes


def test_a_failed_tool_sets_the_error_type_and_status():
    span = tool_span(agent="iris", tool="skill_run", ok=False, error="PermissionError")
    assert span.attributes["error.type"] == "PermissionError"
    assert span.status == "error"
    plain = tool_span(agent="iris", tool="skill_run", ok=False)
    assert plain.attributes["error.type"] == "tool_error"


def test_a_failed_turn_sets_the_error_type():
    span = turn_span(agent="iris", duration_ms=5, error="recursion_limit")
    assert span.attributes["error.type"] == "recursion_limit"
    assert span.status == "error"


def test_every_span_stays_on_convention():
    """The gate: a key the conventions do not define would reach a backend as nothing."""
    spans = [
        turn_span(agent="iris", duration_ms=1, usage={"prompt_tokens": 1}, model="m", error=""),
        tool_span(agent="iris", tool="t", call_id="c", digest="d", ok=True),
        tool_span(agent="iris", tool="t", ok=False, error="boom"),
    ]
    for span in spans:
        assert off_convention(span) == [], span.attributes
        assert set(span.attributes) <= GENAI_ATTRIBUTES


def test_an_off_convention_attribute_is_detected():
    span = Span(name="x", kind="internal", attributes={"gen_ai.usage.prompt_tokens": 5})
    assert off_convention(span) == ["gen_ai.usage.prompt_tokens"]


def test_the_otlp_shape_sorts_keys_and_types_values():
    span = turn_span(agent="iris", duration_ms=2.5, usage={"prompt_tokens": 7, "completion_tokens": 1})
    attrs = span.as_otlp_attributes()
    assert [a["key"] for a in attrs] == sorted(a["key"] for a in attrs)
    by_key = {a["key"]: a["value"] for a in attrs}
    assert by_key["gen_ai.usage.input_tokens"] == {"intValue": "7"}
    assert by_key["iris.turn.duration_ms"] == {"doubleValue": 2.5}
    assert by_key["gen_ai.agent.name"] == {"stringValue": "iris"}


def test_a_boolean_attribute_is_a_boolean():
    span = tool_span(agent="iris", tool="t", side_effecting=False)
    by_key = {a["key"]: a["value"] for a in span.as_otlp_attributes()}
    assert by_key["iris.tool.side_effecting"] == {"boolValue": False}


# ── the exporter seam ───────────────────────────────────────────────────────


def test_the_exporters_are_none_and_otlp():
    assert EXPORTERS == ("none", "otlp")


@pytest.mark.skipif(sdk_available(), reason="the SDK is installed here, so there is nothing to refuse")
def test_asking_for_otlp_without_the_extra_is_a_boot_error():
    """Fail closed: an operator staring at an empty dashboard is the failure mode."""
    with pytest.raises(OtelUnavailable) as exc:
        exporter()
    assert "iris-personal-ai[otel]" in str(exc.value)
    assert "OTEL_EXPORTER=none" in str(exc.value)


@pytest.mark.skipif(not sdk_available(), reason='needs the optional extra: pip install "iris-personal-ai[otel]"')
def test_asking_for_otlp_with_the_extra_builds_a_provider():
    """The other half of the seam: with the extra installed, `OTEL_EXPORTER=otlp`
    gets a real tracer provider — and the service name an operator filters on is
    the one it was built with."""
    provider = exporter(endpoint="http://127.0.0.1:4318/v1/traces", service_name="iris-test")
    try:
        assert provider.get_tracer("iris-test") is not None
        assert provider.resource.attributes["service.name"] == "iris-test"
    finally:
        provider.shutdown()


def test_the_sink_logs_when_there_is_no_tracer(caplog):
    """`sent` distinguishes 'emitted to a provider' from 'logged for a developer'."""
    sink = SpanSink(None)
    with caplog.at_level("DEBUG"):
        sink.emit(turn_span(agent="iris", duration_ms=1))
    assert sink.sent == 0
    assert "no exporter" in caplog.text


class _FakeSpan:
    """The slice of the OTel span API the sink uses — no more, so the test fails
    if the sink starts reaching for something else."""

    def __init__(self) -> None:
        self.attributes: dict = {}
        self.status = None

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def set_status(self, status: object) -> None:
        self.status = status

    def __enter__(self) -> _FakeSpan:
        return self

    def __exit__(self, *exc: object) -> bool:
        return False


class _FakeTracer:
    def __init__(self) -> None:
        self.spans: list = []

    def start_as_current_span(self, name):
        span = _FakeSpan()
        self.spans.append((name, span))
        return span


def test_the_sink_sends_to_a_tracer_and_counts_it():
    tracer = _FakeTracer()
    sink = SpanSink(tracer)
    sink.emit(turn_span(agent="iris", duration_ms=3, usage={"prompt_tokens": 2}))
    assert sink.sent == 1
    (name, span), = tracer.spans
    assert name == "turn"
    assert span.attributes["gen_ai.usage.input_tokens"] == 2


def test_a_span_with_a_stray_attribute_is_cleaned_before_it_is_sent(caplog):
    tracer = _FakeTracer()
    sink = SpanSink(tracer)
    stray = Span(name="x", kind="internal", attributes={"gen_ai.usage.prompt_tokens": 5, "gen_ai.agent.name": "iris"})
    with caplog.at_level("WARNING"):
        sink.emit(stray)
    assert "off-convention" in caplog.text
    (_, sent), = tracer.spans
    assert "gen_ai.usage.prompt_tokens" not in sent.attributes
    assert sent.attributes["gen_ai.agent.name"] == "iris"


# ── the hook ────────────────────────────────────────────────────────────────


def test_the_hook_subscribes_to_the_turn_lifecycle():
    from iris_ai.hooks import HookBus

    bus = HookBus()
    install(bus, agent="iris")
    assert set(bus.subscribers("turn_end")) >= {"otel"}
    assert "otel" in bus.subscribers("pre_tool")


async def test_a_turn_emits_a_turn_span_through_the_bus():
    from iris_ai.hooks import HookBus

    bus = HookBus()
    tracer = _FakeTracer()
    sink = install(bus, agent="custom-agent", tracer=tracer)
    await bus.emit("turn_start")
    await bus.emit("pre_tool", tool="memory_search", args={})
    await bus.emit("post_tool", tool="memory_search", ok=True)
    await bus.emit("turn_end", usage={"prompt_tokens": 11, "completion_tokens": 3})

    names = [name for name, _ in tracer.spans]
    assert names == ["memory_search", "turn"]
    assert sink.sent == 2
    turn_span_attributes = tracer.spans[-1][1].attributes
    assert turn_span_attributes["gen_ai.agent.name"] == "custom-agent"
    assert turn_span_attributes["gen_ai.usage.output_tokens"] == 3


async def test_telemetry_runs_after_policy_but_never_costs_a_turn():
    """A raising telemetry handler must not break the bus (the bus already skips)."""
    from iris_ai.hooks import HookBus

    bus = HookBus()
    seen: list[str] = []

    def policy(**_: object):
        seen.append("policy")

    def telemetry(**_: object):
        raise RuntimeError("backend down")

    bus.on("pre_tool", policy, priority=-100, name="policy")
    bus.on("pre_tool", telemetry, priority=-50, name="otel")
    await bus.emit("pre_tool", tool="memory_search", args={})
    assert seen == ["policy"]
