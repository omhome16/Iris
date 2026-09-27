"""The vocabulary of a screen action — closed, small, and classified.

P7's first decision is that a new *capability* arrives as one tool with an
`action` enum rather than as five tools. This module is that enum's home, so the
tool schema and the permission model cannot disagree about what actions exist.

Two classifications ride on the vocabulary rather than on prose:

- **Destructive.** `click` and `type` change the world in a way a single approval
  should not cover a sequence of. The vault's HITL note makes the case:
  "fill the form" is not consent for the submit that follows. These always
  confirm, even inside a live grant.
- **Credential-touching.** A `type` aimed at a field whose name looks like a
  password, token or OTP. It is already destructive, but naming it lets the audit
  log and the approval prompt say *why*.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class ActionKind(StrEnum):
    """What a computer action does. Four verbs, no more."""

    SCREENSHOT = "screenshot"
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"


# Actions that change state in a way an approval cannot be amortised over.
DESTRUCTIVE_ACTIONS: frozenset[ActionKind] = frozenset({ActionKind.CLICK, ActionKind.TYPE})

# A target that reads like a credential field. Deliberately blunt: a false
# positive costs one extra confirmation; a false negative costs a password.
CREDENTIAL_TARGET_RE = re.compile(
    r"(?i)\b(pass(word|phrase|code)?|token|secret|credential|api[_\-\s]?key|otp|pin)\b"
)


def looks_like_credential_field(target: str) -> bool:
    return bool(CREDENTIAL_TARGET_RE.search(target or ""))


@dataclass(frozen=True, slots=True)
class Action:
    """One requested screen action.

    Field meaning depends on the kind, which is why they are documented rather
    than typed separately:

    - `screenshot` — nothing required.
    - `navigate`   — `target` is the URL; its host is checked against the allowlist.
    - `click`      — `target` is a selector; `window` names the page/app it acts in.
    - `type`       — as `click`, plus `text`.

    `window` exists because an allowlist of *what may be clicked* is meaningless
    without saying *where*: a click in an allowlisted document viewer and a click
    in an unlisted banking tab are different requests.
    """

    kind: ActionKind
    target: str = ""
    window: str = ""
    text: str = ""

    @property
    def destructive(self) -> bool:
        return self.kind in DESTRUCTIVE_ACTIONS

    @property
    def touches_credentials(self) -> bool:
        return self.kind is ActionKind.TYPE and looks_like_credential_field(self.target)


@dataclass(frozen=True, slots=True)
class Observation:
    """What the driver reported, shaped so the graph can act on a refusal.

    `unavailable` is a distinct outcome from `error` on purpose: "there is no
    driver" is a configuration fact the owner can fix, while "the click missed"
    is an action that can be retried. Conflating them is how a missing optional
    dependency becomes a mysterious mid-turn failure.
    """

    kind: ActionKind
    ok: bool = False
    detail: str = ""
    url: str = ""
    title: str = ""
    screenshot: str = ""  # driver-shaped: a data URI or a sandbox path
    error: str = ""
    unavailable: bool = False
    meta: dict = field(default_factory=dict)

    @property
    def refused(self) -> bool:
        return not self.ok
