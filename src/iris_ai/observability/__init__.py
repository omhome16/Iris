"""Observability — spans shaped for the conventions, exported only if asked.

Two halves, split so the interesting part is testable without a telemetry stack:

- `spans.py` shapes a turn and a tool call into spans carrying the GenAI semantic
  conventions' attribute names. It is pure: no SDK, no collector, no network.
- `otel.py` sends them, through the optional `[otel]` extra. Asking for an
  exporter without the SDK is a boot error naming the extra, never a quiet no-op.

The traces (`iris trace`) stay the local, always-on record; spans are what make
the same events legible to a backend an operator already runs.
"""

from __future__ import annotations

from iris_ai.observability.spans import Span, off_convention, tool_span, turn_span

__all__ = ["Span", "off_convention", "tool_span", "turn_span"]
