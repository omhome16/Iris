"""The permission model — the actual security boundary for computer-use.

The audit's position, taken from the vault's agent-harness note, is that the
*documentation of a capability is not its containment*: process isolation is not
a kernel boundary, and a driver is not a permission model. So this module is what
fences P7:

1. **Suffix-matched allowlists, on label boundaries.** `example.com` allows
   `example.com` and `docs.example.com`, and **not** `evil-example.com`. The
   browser-style `*.example.com` spelling is accepted and treated as the label
   rule rather than as a glob, because a glob is how `evil-example.com` slips in.
2. **Confirmation for the destructive subset.** `click` and `type` always confirm
   through the owner's approval interrupt, even inside a live grant.
3. **A per-session grant with an action budget.** One approval authorises at most
   `computer_max_actions` actions; the grant is consumed per action and refills
   only on a fresh approval. A click loop therefore has a bottom.

Empty allowlists mean **nothing is allowed**, not "everything is allowed". That
is the one direction a security default can safely fail in, and it means an owner
who enables computer-use must still say *where* it may go.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from iris_ai.computer.actions import Action, ActionKind


def host_of(target: str) -> str:
    """The lowercase host of a URL or bare host. `""` when there is none."""
    text = (target or "").strip()
    if not text:
        return ""
    parsed = urlsplit(text if "://" in text else f"//{text}")
    return (parsed.hostname or "").lower()


def _normalise(entry: str) -> str:
    return (entry or "").strip().strip(".").lower()


def matches_host(host: str, suffixes: Sequence[str]) -> tuple[bool, str]:
    """Label-boundary suffix match: `example.com` matches `docs.example.com`,
    never `evil-example.com`."""
    value = _normalise(host)
    if not value:
        return False, ""
    for raw in suffixes:
        suffix = _normalise(raw)
        if suffix.startswith("*."):
            suffix = suffix[2:]
        if not suffix:
            continue
        if value == suffix or value.endswith("." + suffix):
            return True, raw
    return False, ""


def matches_title(title: str, suffixes: Sequence[str]) -> tuple[bool, str]:
    """Plain case-insensitive suffix match for window/page titles.

    Titles are prose, not hostnames, so the label rule does not apply: an
    allowlist entry `Example` means a title ending in `Example`, e.g.
    `Sign in — Example`.
    """
    value = (title or "").strip().lower()
    if not value:
        return False, ""
    for raw in suffixes:
        suffix = (raw or "").strip().lower().lstrip("*").lstrip(".")
        if suffix and value.endswith(suffix):
            return True, raw
    return False, ""


@dataclass(frozen=True, slots=True)
class Decision:
    """The permission verdict for one action, before any approval."""

    allowed: bool
    reason: str = ""

    @property
    def refused(self) -> bool:
        return not self.allowed


class Grants:
    """Per-session action budget. `grant()` refills; `consume()` spends one."""

    def __init__(self, max_actions: int) -> None:
        self.max_actions = max(1, int(max_actions))
        self._remaining: dict[str, int] = {}

    def grant(self, session: str) -> int:
        self._remaining[session] = self.max_actions
        return self.max_actions

    def remaining(self, session: str) -> int:
        return self._remaining.get(session, 0)

    def consume(self, session: str) -> bool:
        left = self._remaining.get(session, 0)
        if left <= 0:
            return False
        self._remaining[session] = left - 1
        return True


class PermissionModel:
    """Allowlists + confirmation policy. Pure: it holds no grant state itself."""

    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str] = (),
        allowed_apps: Sequence[str] = (),
        max_actions: int = 12,
        confirm_destructive: bool = True,
    ) -> None:
        self.allowed_hosts = tuple(h for h in (allowed_hosts or ()) if str(h).strip())
        self.allowed_apps = tuple(a for a in (allowed_apps or ()) if str(a).strip())
        self.confirm_destructive = bool(confirm_destructive)
        self.grants = Grants(max_actions)

    @classmethod
    def from_csv(
        cls,
        *,
        allowed_hosts: str = "",
        allowed_apps: str = "",
        max_actions: int = 12,
        confirm_destructive: bool = True,
    ) -> PermissionModel:
        return cls(
            allowed_hosts=[p.strip() for p in (allowed_hosts or "").split(",") if p.strip()],
            allowed_apps=[p.strip() for p in (allowed_apps or "").split(",") if p.strip()],
            max_actions=max_actions,
            confirm_destructive=confirm_destructive,
        )

    def check(self, action: Action) -> Decision:
        """May this action be attempted at all, and in an allowed place?"""
        if action.kind is ActionKind.SCREENSHOT:
            return Decision(True)
        if action.kind is ActionKind.NAVIGATE:
            if not self.allowed_hosts:
                return Decision(False, "no navigation targets are allowed — set computer_allowed_hosts")
            host = host_of(action.target)
            if not host:
                return Decision(False, f"{action.target!r} has no host to check against the allowlist")
            if not matches_host(host, self.allowed_hosts)[0]:
                return Decision(False, f"host {host!r} is not in computer_allowed_hosts")
            return Decision(True)
        # click / type
        if not self.allowed_apps:
            return Decision(False, "no windows or apps are allowed — set computer_allowed_apps")
        if not action.window.strip():
            return Decision(False, f"{action.kind.value} must name the window or page it acts on")
        if not matches_title(action.window, self.allowed_apps)[0]:
            return Decision(False, f"window {action.window!r} is not in computer_allowed_apps")
        return Decision(True)

    def needs_confirmation(self, action: Action) -> bool:
        """Destructive actions always confirm, even inside a live grant.

        A keystroke into a credential-looking field confirms unconditionally: the
        config switch exists for an owner who tires of confirming clicks, and
        `computer_confirm_destructive=False` must not become "type passwords
        without asking".
        """
        if action.touches_credentials:
            return True
        return self.confirm_destructive and action.destructive
