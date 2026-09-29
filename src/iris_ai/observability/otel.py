"""The OTLP transport — optional, and loud when it is asked for but absent.

`spans.py` shapes; this sends. The SDK is an optional extra
(`pip install 'iris-personal-ai[otel]'`) because a local single-user harness must
not require a telemetry stack to start — the same rule that keeps Docker off the
happy path.

Fail closed, not silent: `OTEL_EXPORTER=otlp` with the SDK missing is a **boot
error** naming the extra, not a quiet no-op that leaves an operator staring at an
empty dashboard wondering why nothing arrived.

The hook is the plug-and-play story paying for itself: the harness already emits
`turn_start`, `pre_tool`, `post_tool` and `turn_end` on the bus, so telemetry is a
*subscriber* rather than a new branch in the agent loop.
"""

from __future__ import annotations

import logging
from typing import Any

from iris_ai.observability.spans import Span, tool_span, turn_span

log = logging.getLogger("iris.otel")

#: What `OTEL_EXPORTER` may say.
EXPORTERS: tuple[str, ...] = ("none", "otlp")


class OtelUnavailable(RuntimeError):
    """The exporter was requested and cannot be built. Never silently skipped."""


def sdk_available() -> bool:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except Exception:  # noqa: BLE001 - any import problem means "not installed"
        return False
    return True


def install_extra() -> str:
    return "pip install 'iris-personal-ai[otel]'"


def exporter(*, endpoint: str = "", service_name: str = "iris") -> Any:
    """Build an OTLP span exporter, or raise saying what to install.

    Deliberately not caught anywhere in `src/`: a deployment that asked for
    telemetry and did not get it should fail where an operator sees it.
    """
    if not sdk_available():
        raise OtelUnavailable(
            f"OTEL_EXPORTER requires the OpenTelemetry SDK, which is not installed ({install_extra()}), "
            "or set OTEL_EXPORTER=none to run without telemetry."
        )
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({"service.name": service_name})
    provider = TracerProvider(resource=resource)
    span_exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
    provider.add_span_processor(BatchSpanProcessor(span_exporter))
    return provider


class SpanSink:
    """Where shaped spans go: an optional real tracer, else the log at debug.

    A sink rather than a hard dependency so the same hook serves a deployment with
    a collector and a developer who only wants `IRIS_DEBUG=1` to show the spans
    that *would* have been sent. `sent` counts what went to a provider, so a test
    can tell "emitted" from "logged".
    """

    def __init__(self, tracer: Any | None = None) -> None:
        self.tracer = tracer
        self.sent = 0

    def emit(self, span: Span) -> None:
        from iris_ai.observability.spans import off_convention

        stray = off_convention(span)
        if stray:
            # A guard rather than a comment: an attribute the conventions do not
            # define arrives at a backend as nothing at all, which is the worst
            # kind of wrong.
            log.warning("dropping off-convention span attributes %s", stray)
            span = Span(
                name=span.name,
                kind=span.kind,
                attributes={k: v for k, v in span.attributes.items() if k not in stray},
                status=span.status,
            )
        if self.tracer is None:
            log.debug("span (no exporter): %s %s", span.name, span.attributes)
            return
        with self.tracer.start_as_current_span(span.name) as otel_span:
            for key, value in span.attributes.items():
                otel_span.set_attribute(key, value)
            if span.status == "error":
                from opentelemetry.trace import Status, StatusCode

                otel_span.set_status(Status(StatusCode.ERROR))
        self.sent += 1


def install(hooks: Any, *, agent: str, tracer: Any | None = None) -> SpanSink:
    """Subscribe a span emitter to the turn lifecycle.

    Negative priority like the guards: telemetry observes, so it runs before an
    add-on hook but never in front of built-in policy. Every handler swallows its
    own errors — a tracing backend that is down must not cost a reply.
    """
    sink = SpanSink(tracer)
    state: dict[str, float] = {}

    def _duration(name: str) -> float:
        import time

        started = state.pop(name, None)
        return (time.monotonic() - started) * 1000 if started else 0.0

    def on_turn_start() -> None:
        import time

        state["turn"] = time.monotonic()

    def on_tool_pre(tool: str, args: Any = None) -> None:
        import time

        state[f"tool:{tool}"] = time.monotonic()

    def on_tool_post(tool: str, ok: bool = True) -> None:
        try:
            sink.emit(tool_span(agent=agent, tool=tool, ok=ok))
        except Exception as exc:  # noqa: BLE001 - telemetry never costs a turn
            log.debug("could not emit a tool span: %s", exc)
        _duration(f"tool:{tool}")

    def on_turn_end(usage: dict | None = None) -> None:
        try:
            sink.emit(turn_span(agent=agent, duration_ms=_duration("turn"), usage=usage))
        except Exception as exc:  # noqa: BLE001
            log.debug("could not emit a turn span: %s", exc)

    hooks.on("turn_start", on_turn_start, priority=-50, name="otel")
    hooks.on("pre_tool", on_tool_pre, priority=-50, name="otel")
    hooks.on("post_tool", on_tool_post, priority=-50, name="otel")
    hooks.on("turn_end", on_turn_end, priority=-50, name="otel")
    return sink


__all__ = ["EXPORTERS", "OtelUnavailable", "SpanSink", "exporter", "install", "install_extra", "sdk_available"]
