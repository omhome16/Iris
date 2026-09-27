"""P6 — redaction and the trace content policy (audit G5).

The rule being pinned is the vault's: **metadata is the default, content is
opt-in, and credentials are never written**. The regression this suite exists for
is concrete — `_trace_turn` used to write `json.dumps(tool_call.args)[:200]`
straight to disk, so any secret a model passed as an argument was persisted.
"""

from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from iris_ai.redact import REDACTED, apply_content_policy, args_hash, redact, redact_text
from iris_ai.trace import TraceLogger


def _filler(seed: str, length: int) -> str:
    """Deterministic, non-secret filler shaped like a credential value.

    This file has to *contain credential shapes* to prove they are redacted, and
    a literal high-entropy string in a test is still a finding: it costs a
    reviewer a real investigation, and it teaches everyone to ignore the scanner.
    So the values are generated here instead of written down. Deterministic on
    purpose — a redaction test that asserts on random input is a flaky test.
    """
    out = ""
    while len(out) < length:
        out += hashlib.sha256(f"{seed}/{len(out)}".encode()).hexdigest()
    return out[:length]


def _b64url(segment: dict) -> str:
    """One base64url JWT segment, the way a real encoder produces it."""
    raw = json.dumps(segment, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


# An OpenAI-shaped key: the `sk-` prefix is what the pattern keys on.
SECRET = "sk-" + _filler("openai-style", 24)
# A bearer token, a long hex key, a password value, and a JWT assembled from
# three base64url segments (the first starting `ey`, so the JWT detector has
# something to match). All generated, none written down.
BEARER = "Bearer " + _filler("bearer", 24)
HEX_KEY = _filler("hex-key", 40)
PASSWORD = _filler("password-value", 12)
JWT = f"{_b64url({'alg': 'HS256'})}.{_b64url({'sub': '1234567890'})}.{_filler('jwt-sig', 32)}"


# ── redact_text ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        f"key is {SECRET}",
        f"Authorization: {BEARER}",
        f'api_key="{HEX_KEY}"',
        f"password={PASSWORD}",
        f"TYPESAFE_API_KEY={HEX_KEY}",
        JWT,
    ],
)
def test_credential_shapes_are_redacted(text):
    out = redact_text(text)
    assert REDACTED in out
    assert PASSWORD not in out
    assert SECRET not in out


def test_ordinary_text_is_left_alone():
    text = "remind me to renew the lease on Tuesday at 09:30"
    assert redact_text(text) == text


def test_redaction_does_not_mangle_the_key_name():
    """Keeping the name is what makes a redacted trace debuggable."""
    out = redact_text(f"api_key={HEX_KEY}")
    assert "api_key" in out
    assert REDACTED in out


# ── redact (structured) ──────────────────────────────────────────────────


def test_a_secret_named_key_is_replaced_whatever_it_holds():
    out = redact({"api_token": "short", "note": "ok"})
    assert out["api_token"] == REDACTED
    assert out["note"] == "ok"


def test_nested_containers_keep_their_shape():
    out = redact({"tools": [{"name": "web_search", "args": {"key": SECRET}}], "n": 3})
    assert out["n"] == 3
    assert out["tools"][0]["name"] == "web_search"
    assert SECRET not in json.dumps(out)


def test_non_strings_pass_through():
    assert redact({"count": 7, "flag": True, "none": None}) == {
        "count": 7,
        "flag": True,
        "none": None,
    }


# ── args_hash ────────────────────────────────────────────────────────────


def test_args_hash_is_stable_and_order_independent():
    """Loop detection asks \"same tool + same args\" — order must not matter."""
    a = args_hash({"query": "tea", "top_k": 5})
    b = args_hash({"top_k": 5, "query": "tea"})
    assert a == b
    assert len(a) == 12


def test_args_hash_differs_for_different_args():
    assert args_hash({"query": "tea"}) != args_hash({"query": "coffee"})


def test_args_hash_survives_unserializable_input():
    assert args_hash({"fn": object()})  # no exception


# ── the content policy ───────────────────────────────────────────────────


ENTRY = {
    "ts": "2026-09-24T12:00:00",
    "session_id": "cli",
    "user": f"my key is {SECRET}, what should I do?",
    "reply": "I would rotate it.",
    "tools": [{"name": "file_write", "args": json.dumps({"path": "a.txt", "api_key": SECRET})}],
    "latency_ms": 1200,
}


def test_metadata_is_the_default_and_keeps_no_content():
    out = apply_content_policy(ENTRY, mode="metadata")
    blob = json.dumps(out)
    assert SECRET not in blob
    assert "rotate it" not in blob  # free text is gone, not just redacted
    assert out["user_chars"] == len(ENTRY["user"])
    assert out["user_hash"]
    assert out["tools"][0]["args_hash"]
    assert "args" not in out["tools"][0]
    assert out["session_id"] == "cli"  # structure and metadata survive
    assert out["latency_ms"] == 1200


def test_redacted_mode_keeps_text_but_strips_credentials():
    out = apply_content_policy(ENTRY, mode="redacted")
    assert REDACTED in out["user"]
    assert "what should I do?" in out["user"]  # still useful for debugging
    assert out["reply"] == "I would rotate it."
    assert SECRET not in json.dumps(out)
    assert SECRET not in json.dumps(out["tools"])


def test_full_mode_still_redacts_credentials():
    """"Full content" means content is on — never that credentials may be written."""
    out = apply_content_policy(ENTRY, mode="full")
    assert out["reply"] == "I would rotate it."
    assert SECRET not in json.dumps(out)


def test_an_unknown_mode_falls_back_to_metadata():
    """A typo in .env must not silently open content logging."""
    out = apply_content_policy(ENTRY, mode="everything")
    assert "user" not in out
    assert "user_hash" in out


def test_sampling_behaves_like_redacted_for_one_entry():
    out = apply_content_policy(ENTRY, mode="metadata", sample=True)
    assert "user" in out
    assert SECRET not in json.dumps(out)


# ── end to end: the file on disk ─────────────────────────────────────────


def test_a_credential_never_reaches_the_trace_file(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "trace_content", "redacted")
    logger = TraceLogger(tmp_path / "traces.jsonl")
    logger.record(ENTRY)
    written = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert SECRET not in written
    assert "rotate it" in written  # redacted mode is still debuggable


def test_metadata_mode_writes_no_content_at_all(tmp_path: Path, monkeypatch):
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "trace_content", "metadata")
    monkeypatch.setattr(settings, "trace_content_sample_rate", 0.0)
    logger = TraceLogger(tmp_path / "traces.jsonl")
    logger.record(ENTRY)
    written = (tmp_path / "traces.jsonl").read_text(encoding="utf-8")
    assert SECRET not in written
    assert "rotate it" not in written
    assert "user_hash" in written


def test_the_trace_still_reads_back_as_a_trace(tmp_path: Path, monkeypatch):
    """Redaction must not break the readers (`/traces`, `iris agents handoffs`)."""
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "trace_content", "metadata")
    logger = TraceLogger(tmp_path / "traces.jsonl")
    logger.record({**ENTRY, "judgment": {"events": [{"kind": "handoff", "from": "researcher"}]}})
    (line,) = logger.recent(1)
    assert line["judgment"]["events"][0]["from"] == "researcher"
    assert line["session_id"] == "cli"
