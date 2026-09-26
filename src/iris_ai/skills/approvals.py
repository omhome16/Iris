"""Approval pins for third-party skill manifests — the rug-pull guard.

The threat is the vault's MCP-security case: *a previously trusted tool starts
behaving differently after an update*. A skill from `package:` (an installed
distribution) or `extra` (a configured root this workspace does not own) used to
be trusted unconditionally — so whoever can write that file can rewrite what the
agent is told to do, because in a skill the manifest body *is* the instruction.

The pin is the sha256 of the **raw manifest text**, not of the parsed fields.
Parsing is deliberately lossy (unknown keys are ignored, extra whitespace
collapses into a list), so a digest over parsed values would not see a change
that still lands in the prompt. Any byte change has to be visible.

The ledger is one small JSON file, `workspace/config/skill_approvals.json`:

    {"package:iris-extras/from-a-package": {"sha256": "9f2c…", "approved_at": "…"}}

Two behaviours, and the asymmetry is the point:

- **First sighting records the digest** and trusts it (trust on first use).
  Third-party skills are installed by an owner who has already decided to trust
  the package; demanding a wall of approvals on first boot would only train
  someone to approve without reading, which is worse than not asking.
- **A changed digest is an error** — a `ValidationIssue("error", …)` that
  disables the skill until `iris skills approve <name>` re-pins the new content.
  Refusing is the safe side: a genuinely updated skill costs one command, a
  silently rewritten instruction costs the trust the whole tier rests on.

Scope, stated plainly: this pins the manifest, **not** the `scripts/` payload.
Scripts already stop at a per-run judgment and an explicit gate
(`skill_script_require_approval`, `skill_guard_gate`), so a changed script is a
decision at call time rather than a trust decision at load time.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

# Sources whose content this workspace does not own. `package:<dist>` is an
# installed distribution; `extra` is a directory named by `SKILLS_EXTRA_DIRS`.
# `builtin` is deliberately absent: it ships inside the wheel the owner
# installed, and the registry already refuses to let anything shadow it.
_PINNED_EXACT = frozenset({"extra"})
_PINNED_PREFIXES = ("package:",)


class ApprovalLedgerError(RuntimeError):
    """The ledger exists but cannot be trusted to answer (corrupt, not a dict).

    Raised rather than treated as empty on purpose: an empty ledger *auto-trusts*
    whatever is on disk now, so a corrupt file would silently re-approve a
    manifest nobody has read.
    """


def is_pinned(source: str) -> bool:
    """True when `source` is third-party and therefore hash-pinned."""
    return source in _PINNED_EXACT or source.startswith(_PINNED_PREFIXES)


def manifest_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def approval_key(source: str, name: str) -> str:
    """The ledger key: `source/name`, e.g. `package:iris-extras/from-a-package`."""
    return f"{source}/{name}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True, slots=True)
class ApprovalCheck:
    """What the ledger says about one manifest.

    `status` is one of:

    - `unchanged` — the digest matches the pin; nothing to do.
    - `first-sight` — no pin existed, and one was just recorded
      (`trust_first_sighting`); the skill stays usable.
    - `unapproved` — no pin existed and trust-on-first-use is **off**, so this
      skill is waiting for an owner's `approve`. **Refuse the skill.**
    - `changed` — a pin exists and differs. **Refuse the skill.**
    - `unreadable` — the ledger could not be parsed. **Refuse the skill.**

    The refusal statuses are distinct on purpose: `changed` means "someone
    rewrote this", `unapproved` means "nobody has looked yet". Collapsing them
    would make `approve` look like the fix for a problem it cannot detect.
    """

    status: str
    digest: str
    approved: str = ""  # the digest previously pinned, "" when there was none


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    key: str
    digest: str
    approved_at: str


class SkillApprovalStore:
    """The on-disk ledger. Reads are cheap; writes only ever record a pin."""

    def __init__(self, path: Path | str, *, trust_first_sighting: bool = True) -> None:
        self.path = Path(path)
        self.trust_first_sighting = trust_first_sighting

    # ── reading ──────────────────────────────────────────────────────────
    def _read(self) -> dict[str, dict[str, str]]:
        """Raises `ApprovalLedgerError` when the file cannot be trusted."""
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApprovalLedgerError(f"{self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ApprovalLedgerError(f"{self.path}: expected a JSON object of pins")
        out: dict[str, dict[str, str]] = {}
        for key, value in data.items():
            if not isinstance(value, dict) or not isinstance(value.get("sha256"), str):
                raise ApprovalLedgerError(f"{self.path}: entry {key!r} is not {{'sha256': …}}")
            out[str(key)] = value
        return out

    def check(self, key: str, text: str) -> ApprovalCheck:
        """Compare a manifest against its pin, recording a first sighting.

        This is the one place the ledger is written during a *read* of the
        registry, and it is deliberate: trust-on-first-use requires observing
        the content once. Nothing else about a skill is ever written here.
        """
        digest = manifest_digest(text)
        try:
            ledger = self._read()
        except ApprovalLedgerError:
            return ApprovalCheck("unreadable", digest)
        prior = (ledger.get(key) or {}).get("sha256", "")
        if not prior:
            if self.trust_first_sighting:
                self.record(key, text)
                return ApprovalCheck("first-sight", digest, digest)
            return ApprovalCheck("unapproved", digest)
        if prior == digest:
            return ApprovalCheck("unchanged", digest, prior)
        return ApprovalCheck("changed", digest, prior)

    def pinned(self, key: str) -> str:
        """The digest currently approved for `key`, or "" (raises if unreadable)."""
        return (self._read().get(key) or {}).get("sha256", "")

    # ── writing ──────────────────────────────────────────────────────────
    def record(self, key: str, text: str) -> ApprovalRecord:
        """Pin `text` as the approved content for `key`.

        Raises `ApprovalLedgerError` when the existing ledger is unreadable —
        overwriting it would discard every other pin, and the owner needs to see
        the problem rather than have it silently resolved in one direction.
        """
        digest = manifest_digest(text)
        ledger = self._read()
        approved_at = _now()
        ledger[key] = {"sha256": digest, "approved_at": approved_at}
        self._write(ledger)
        return ApprovalRecord(key=key, digest=digest, approved_at=approved_at)

    def _write(self, ledger: dict[str, dict[str, str]]) -> None:
        # Atomic like every other workspace write: a half-written ledger would
        # read as corrupt, which (by design) disables every pinned skill.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(ledger, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(tmp, self.path)
