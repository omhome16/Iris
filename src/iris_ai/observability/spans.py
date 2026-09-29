"""Spans, shaped for the GenAI semantic conventions.

Observability that speaks its own dialect is observability nobody reads: an
operator already has a backend with dashboards, and the conventions are what make
Iris's turns and tool calls land in it as first-class spans instead of opaque
blobs.

This module is **pure**. It shapes spans — name, kind, attributes, status — from
events the harness already has (a turn's usage, a tool call's outcome), so the
mapping is unit-testable with no SDK, no collector and no network. The transport
lives next door in `iris_ai.observability.otel`, and it is optional on purpose:
`pip install 'iris-personal-ai[otel]'`.

Attributes used, all from the conventions (GenAI and the general ones):

| attribute | where it comes from |
|---|---|
| `gen_ai.operation.name` | `invoke_agent` per turn, `execute_tool` per tool call |
| `gen_ai.agent.name` | the harness's agent name (`settings.agent_name`) |
| `gen_ai.tool.name` / `.call.id` | the tool call as the model asked for it |
| `gen_ai.request.model` | the model the turn ran on, when the graph knows it |
| `gen_ai.usage.input_tokens` / `.output_tokens` | the turn's usage snapshot |
| `error.type` | set only when the span failed |

One deliberate omission, stated rather than implied: **tool arguments are not an
attribute.** They are hashed into `iris.tool.digest` instead, for the same reason
the traces hash them — a credential in a tool argument must not become a span
attribute in someone's SaaS backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: The attribute keys this module will ever emit. A test asserts every span's
#: attributes are a subset, so a typo like `gen_ai.usage.prompt_tokens` (which the
#: conventions do not define) cannot reach a backend and silently show up as
#: nothing.
GENAI_ATTRIBUTES: frozenset[str] = frozenset(
    {
        "gen_ai.operation.name",
        "gen_ai.agent.name",
        "gen_ai.tool.name",
        "gen_ai.tool.call.id",
        "gen_ai.request.model",
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens",
        "error.type",
        "iris.turn.duration_ms",
        "iris.tool.digest",
        "iris.tool.side_effecting",
    }
)

TURN_SPAN = "invoke_agent"
TOOL_SPAN = "execute_tool"


@dataclass(frozen=True, slots=True)
class Span:
    """One span, as data. `status` is `"ok"` or `"error"`."""

    name: str
    kind: str
    attributes: dict[str, Any] = field(default_factory=dict)
    status: str = "ok"

    def as_otlp_attributes(self) -> list[dict[str, Any]]:
        """The OTLP `KeyValue` list, which is what an exporter actually sends.

        Sorted by key so a test can compare two spans' wire shapes directly, and
        so a diff of recorded telemetry is readable.
        """
        out: list[dict[str, Any]] = []
        for key in sorted(self.attributes):
            value = self.attributes[key]
            if isinstance(value, bool):
                out.append({"key": key, "value": {"boolValue": value}})
            elif isinstance(value, int):
                out.append({"key": key, "value": {"intValue": str(value)}})
            elif isinstance(value, float):
                out.append({"key": key, "value": {"doubleValue": value}})
            else:
                out.append({"key": key, "value": {"stringValue": str(value)}})
        return out


def turn_span(
    *,
    agent: str,
    duration_ms: float,
    usage: dict[str, Any] | None = None,
    model: str = "",
    error: str = "",
) -> Span:
    """One turn. Usage rides here until the model call is its own span (a later phase).

    `gen_ai.usage.input_tokens` on an aggregate span is the convention's own
    attribute name used for the sum of the calls underneath it, which is the
    honest reading: the turn spent that much.
    """
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": TURN_SPAN,
        "gen_ai.agent.name": agent,
        "iris.turn.duration_ms": round(float(duration_ms), 3),
    }
    if model:
        attributes["gen_ai.request.model"] = model
    usage = usage or {}
    if usage:
        attributes["gen_ai.usage.input_tokens"] = int(usage.get("prompt_tokens", 0) or 0)
        attributes["gen_ai.usage.output_tokens"] = int(usage.get("completion_tokens", 0) or 0)
    if error:
        attributes["error.type"] = error
    return Span(name="turn", kind="internal", attributes=attributes, status="error" if error else "ok")


def tool_span(
    *,
    agent: str,
    tool: str,
    call_id: str = "",
    digest: str = "",
    ok: bool = True,
    side_effecting: bool = True,
    error: str = "",
) -> Span:
    """One tool call. The arguments are a digest, never an attribute."""
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": TOOL_SPAN,
        "gen_ai.agent.name": agent,
        "gen_ai.tool.name": tool,
        "iris.tool.side_effecting": bool(side_effecting),
    }
    if call_id:
        attributes["gen_ai.tool.call.id"] = call_id
    if digest:
        attributes["iris.tool.digest"] = digest
    if error or not ok:
        attributes["error.type"] = error or "tool_error"
    return Span(name=tool, kind="internal", attributes=attributes, status="ok" if ok and not error else "error")


def off_convention(span: Span) -> list[str]:
    """Attribute keys outside the conventions. Empty means "safe to emit"."""
    return sorted(key for key in span.attributes if key not in GENAI_ATTRIBUTES)


__all__ = [
    "GENAI_ATTRIBUTES",
    "TOOL_SPAN",
    "TURN_SPAN",
    "Span",
    "off_convention",
    "tool_span",
    "turn_span",
]
