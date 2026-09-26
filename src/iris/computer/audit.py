"""The action audit log — what Iris did to a screen, and never what it typed.

An append-only `config/actions.jsonl`, one line per attempted action, written
through P6's redaction so a credential in a URL or a window title is stripped
before it reaches disk.

The one rule that shapes the whole module: **an action log that contains what was
typed is a keylogger.** A `type` action therefore records the *length* and a
*salted-free digest* of the text, never the text — enough to correlate two
recordings of the same input, and useless for recovering it.

Recording is best-effort by design: a failed write must not take down an action
that already happened, and it must not turn a successful navigation into an
error. It logs a warning and moves on. Counts matter more than perfection here —
which is exactly why the log is not the enforcement path. Permission and budget
are enforced in `permissions`/`session`; this file is the record.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from iris.computer.actions import Action, Observation
from iris.redact import args_hash, redact

log = logging.getLogger("iris")


class ActionLog:
    """Append-only JSONL of attempted computer actions."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def record(
        self,
        action: Action,
        observation: Observation,
        *,
        session: str = "",
        decision: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict:
        """Write one entry and return it (so callers/tests can assert the shape)."""
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(),
            "session": session,
            "action": action.kind.value,
            "target": action.target,
            "window": action.window,
            "decision": decision,
            "ok": observation.ok,
            "detail": observation.detail,
            "url": observation.url,
            "title": observation.title,
            "error": observation.error,
            "unavailable": observation.unavailable,
            "destructive": action.destructive,
            "credentials": action.touches_credentials,
        }
        if action.text:
            # Never the text itself. Length + digest correlate repeats without
            # being recoverable — see the module docstring.
            entry["text_chars"] = len(action.text)
            entry["text_hash"] = args_hash(action.text)
        if extra:
            entry.update(extra)
        entry = redact(entry)

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError as exc:
            # The action already happened; the record failing must not compound it.
            log.warning("could not write the computer audit log: %s", exc)
        return entry

    def recent(self, limit: int = 20) -> list[dict]:
        """The newest entries first, for `GET /actions` and `iris tools actions`.

        A truncated or hand-edited line is skipped rather than raising: a log the
        owner cannot read is worse than a log with one missing row.
        """
        if not self.path.exists():
            return []
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            log.warning("could not read the computer audit log: %s", exc)
            return []
        out: list[dict] = []
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
            if len(out) >= max(1, limit):
                break
        return out
