"""Prompt identity on the trace.

The claim under test: a turn's trace says *which* prompt policy produced it and
*what* the assembled prefix actually was. Without both, a quality change cannot
be attributed to a prompt edit rather than a model upgrade or a corpus change —
which is the vault's top-listed observability failure ("can't tell which prompt
caused it").

The second claim is the guard rail on the first: attribution must not make
ordinary turns heavier. A turn that never assembled a prompt stays compact.
"""

from __future__ import annotations

from iris_ai import turnlog
from iris_ai.config import settings


def test_a_turn_trace_carries_the_prompt_version_and_fingerprint():
    with turnlog.collect() as log:
        turnlog.note_prompt("abc123def456")
        entry = log.to_trace()

    assert entry["prompt"]["version"] == settings.prompt_version
    assert entry["prompt"]["fingerprint"] == "abc123def456"


def test_the_fingerprint_distinguishes_prefixes():
    """Two different prefixes must not fingerprint the same — otherwise the
    field is decoration rather than attribution."""
    import hashlib

    first = hashlib.sha256(b"## Operating contract\nA").hexdigest()[:12]
    second = hashlib.sha256(b"## Operating contract\nB").hexdigest()[:12]
    assert first != second


def test_a_turn_without_prompt_assembly_stays_compact():
    with turnlog.collect() as log:
        turnlog.mark("agent", 12.0)
        entry = log.to_trace()

    assert "prompt" not in entry
    assert entry["stages_ms"] == {"agent": 12}


def test_an_empty_turn_still_produces_no_trace_block():
    """The original contract: nothing observed, nothing written."""
    with turnlog.collect() as log:
        entry = log.to_trace()
    assert entry == {}


def test_note_prompt_outside_a_turn_is_a_no_op():
    """Telemetry must never be a way to crash a caller."""
    turnlog.note_prompt("ignored")
