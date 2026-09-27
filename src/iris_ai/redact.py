"""Redaction and the trace content policy.

The vault's rule (MOC 07, Observability and Tracing) is that **metadata is the
default and content is opt-in** — counts, hashes, IDs, scores and timings are
always safe, while free text is a privacy and cost decision. Iris was
metadata-only *by accident* and redacted nothing: `_trace_turn` wrote
`json.dumps(tool_call.args)[:200]` straight to disk.

Two things live here, and only two:

1. **`redact()`** — the single way a value becomes trace-safe. It is deliberately
   blunt and pattern-based: it cannot be complete, and it does not pretend to be.
   What it guarantees is that a credential is never *voluntarily* written by the
   code paths that record telemetry.
2. **`apply_content_policy()`** — the one place the `metadata` / `redacted` /
   `full` decision is made, so every writer gets the same treatment rather than
   each one re-deciding.

`args_hash()` exists because loop detection and replay safety need to know
*whether* two calls had the same arguments without the trace storing them.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

REDACTED = "[redacted]"

# Values that look like credentials. Blunt on purpose — a false positive costs a
# redacted word in a trace; a false negative costs a leaked key.
_VALUE_PATTERNS = (
    re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{8,}"),          # OpenAI-style
    re.compile(r"\bBearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"\b(?:xox[baprs]-)[A-Za-z0-9\-]{8,}"),          # Slack
    re.compile(r"\bey[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),  # JWT
    re.compile(r"\b[A-Fa-f0-9]{32,}\b"),                        # long hex blobs (keys, tokens)
    re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b"),                # long base64 blobs
)
# `password=...`, `api_key: ...`, `TOKEN="..."` inside a free-text value.
_KV_PATTERN = re.compile(
    r"(?i)\b([A-Za-z0-9_\-]*(?:api[_\-]?key|token|secret|password|passwd|credential)[A-Za-z0-9_\-]*)"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)
# Keys whose value is a secret by name, whatever it contains.
SECRET_KEY_RE = re.compile(r"(?i)(api[_\-]?key|token|secret|password|passwd|credential|auth)")


def redact_text(text: str) -> str:
    """Strip credential-shaped substrings out of one string."""
    if not text:
        return text
    out = _KV_PATTERN.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    for pattern in _VALUE_PATTERNS:
        out = pattern.sub(REDACTED, out)
    return out


def redact(value: Any, *, key: str = "") -> Any:
    """Recursively redact a JSON-ish value.

    A key that *names* a secret is replaced wholesale, whatever its value looks
    like; otherwise strings are scanned for credential shapes. Containers keep
    their shape so a trace stays readable.
    """
    if isinstance(value, dict):
        return {k: redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, key=key) for v in value]
    if isinstance(value, str):
        if key and SECRET_KEY_RE.search(key):
            return REDACTED
        return redact_text(value)
    return value


def args_hash(args: Any) -> str:
    """A stable short digest of a tool call's arguments.

    Sort keys so the same logical arguments hash the same regardless of order —
    which is what makes this usable for loop detection (`same tool + same args
    ≥3×`) without storing the arguments themselves.
    """
    try:
        canonical = json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canonical = repr(args)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]


def _digest(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def apply_content_policy(entry: dict, *, mode: str = "", sample: bool = False) -> dict:
    """The one place the trace content policy is decided.

    - **metadata** (default): free text is replaced by its length and a hash;
      tool calls keep their name and gain an `args_hash` instead of `args`.
    - **redacted**: free text is kept, with credential shapes stripped.
    - **full**: as written, still redacted — "full" means *content is on*, never
      "credentials may be written".

    `sample=True` is the documented compromise (full-ish content for a fraction
    of traffic); it behaves like `redacted` for one entry.
    """
    from iris_ai.config import settings

    mode = (mode or settings.trace_content or "metadata").lower()
    if sample:
        mode = "redacted"
    if mode not in ("metadata", "redacted", "full"):
        mode = "metadata"

    out: dict[str, Any] = {}
    for key, value in entry.items():
        if key in ("user", "reply"):
            text = str(value or "")
            if mode == "metadata":
                out[f"{key}_chars"] = len(text)
                out[f"{key}_hash"] = _digest(text)
            else:
                out[key] = redact_text(text)
            continue
        if key == "tools" and isinstance(value, list):
            tools: list[dict] = []
            for call in value:
                if not isinstance(call, dict):
                    continue
                item: dict[str, Any] = {"name": call.get("name", "")}
                raw = call.get("args")
                if mode == "metadata":
                    item["args_hash"] = args_hash(raw)
                    item["args_chars"] = len(str(raw or ""))
                else:
                    item["args"] = redact(raw)
                tools.append(item)
            out[key] = tools
            continue
        out[key] = redact(value, key=str(key))
    return out
