"""Tool classes, default policy, and the visible surface — declared, not inferred.

Two hand-maintained sets used to govern tool safety: `NON_OWNER_BLOCKED` (four
names) and `READ_ONLY_TOOLS` (the research subagent's allowlist). Both are
allowlists with no default, so adding a tool meant remembering to edit them, and
forgetting was silent. This module replaces that shape with a declaration.

This module deliberately imports **nothing** from `iris`: it is leaf data plus
pure rules. `iris_ai.agent.tools` depends on it, not the other way round, and the
two are kept honest by a coverage test rather than by an import.

Rules, in the order they apply:

1. **Every tool declares a class.** A declaration test asserts the table covers
   `TOOL_NAMES` in *both* directions, so a new tool cannot arrive unclassified.
2. **The class yields a default policy.** `credentialed` and `control` default to
   `ask`; the rest default to `allow`, which preserves shipped behaviour. Iris is
   a single-owner personal assistant and the conformance audit records that as a
   deliberate deviation from fail-closed-for-everything — packages are not
   approval-gated, and an approval on every memory write is hostile.
3. **Most specific wins, and `deny` always wins.** A per-tool override beats a
   class override beats the class default. But if *any* applicable rule says
   deny, the result is deny — a class-wide deny cannot be quietly re-opened one
   tool at a time.

The surface half of the module answers a different question: what goes in front
of the model. `surface="core"` tools are never deferred; `extended` tools are
deferred, last-declared first, once the visible set exceeds `tool_surface_budget`.
Deferral is **presentation, not permission** — a deferred tool is still callable
by name — and promotion (a connected channel, an active skill) beats the budget,
because a channel's own tools are not optional. A judgment nobody can inspect is
indistinguishable from one that silently failed, so both the policy and the
deferral are readable from `iris tools`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum


class ToolClass(StrEnum):
    """What kind of side effect a tool can have. Drives the default policy."""

    READ = "read"  # no side effect: memory, files, search, inspection
    FILESYSTEM = "filesystem"  # writes inside the sandbox only
    MEMORY_WRITE = "memory_write"  # durable memory, skills, dreams
    NETWORK = "network"  # fetches from the outside world
    CREDENTIALED = "credentialed"  # acts using a provider key (none today)
    DELIVERY = "delivery"  # sends to a human (Telegram, broadcast)
    CONTROL = "control"  # drives a browser or a desktop


class Policy(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# Iris's stance, written down instead of implied. The two classes that can touch
# something outside Iris's own data default to `ask`; everything else keeps the
# behaviour that shipped, because the audit records it as intentional.
CLASS_DEFAULTS: dict[ToolClass, Policy] = {
    ToolClass.READ: Policy.ALLOW,
    ToolClass.FILESYSTEM: Policy.ALLOW,
    ToolClass.MEMORY_WRITE: Policy.ALLOW,
    ToolClass.NETWORK: Policy.ALLOW,
    ToolClass.DELIVERY: Policy.ALLOW,
    ToolClass.CREDENTIALED: Policy.ASK,
    ToolClass.CONTROL: Policy.ASK,
}


@dataclass(frozen=True, slots=True)
class Declaration:
    """One tool's declared class and visibility."""

    cls: ToolClass
    surface: str = "core"  # "core" (never deferred) | "extended" (deferrable)


# The declaration table. Extended tools are listed in *keep* preference order:
# deferral takes them from the end, so the earlier a tool is declared here the
# longer it stays visible when the budget binds.
#
# core — the agent's working set. None of these may ever be hidden: hiding
#        `memory_search` to save prompt tokens would be trading the product's
#        whole point for schema bytes.
# extended — capabilities that are real but situational, reached through
#        `find_tools` when the surface is over budget.
TOOL_DECLARATIONS: dict[str, Declaration] = {
    "memory_search": Declaration(ToolClass.READ),
    "deep_dive": Declaration(ToolClass.READ),
    "verify_answer": Declaration(ToolClass.READ),
    "remember": Declaration(ToolClass.MEMORY_WRITE),
    "note": Declaration(ToolClass.MEMORY_WRITE),
    "inspect_mind": Declaration(ToolClass.READ),
    "forget": Declaration(ToolClass.MEMORY_WRITE),
    "file_create": Declaration(ToolClass.FILESYSTEM),
    "file_write": Declaration(ToolClass.FILESYSTEM),
    "file_read": Declaration(ToolClass.READ),
    "file_list": Declaration(ToolClass.READ),
    "web_search": Declaration(ToolClass.NETWORK),
    "ingest_url": Declaration(ToolClass.NETWORK),
    "skill_list": Declaration(ToolClass.READ),
    "skill_apply": Declaration(ToolClass.MEMORY_WRITE),
    "schedule_task": Declaration(ToolClass.MEMORY_WRITE),
    "find_tools": Declaration(ToolClass.READ),
    # ── extended ────────────────────────────────────────────────────────
    "computer": Declaration(ToolClass.CONTROL, "extended"),
    "skill_run": Declaration(ToolClass.MEMORY_WRITE, "extended"),
    "skill_write": Declaration(ToolClass.MEMORY_WRITE, "extended"),
    "skill_revise": Declaration(ToolClass.MEMORY_WRITE, "extended"),
    "dream_now": Declaration(ToolClass.MEMORY_WRITE, "extended"),
    "get_chat_history": Declaration(ToolClass.READ, "extended"),
    "send_message": Declaration(ToolClass.DELIVERY, "extended"),
    "send_photo": Declaration(ToolClass.DELIVERY, "extended"),
}


class PolicyError(ValueError):
    """A config override that cannot be honoured. Never silently ignored."""


@dataclass(frozen=True, slots=True)
class ToolDecision:
    tool: str
    cls: ToolClass
    policy: Policy
    source: str  # "override:tool" | "override:class" | "class-default"
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.policy is Policy.ALLOW

    @property
    def needs_approval(self) -> bool:
        return self.policy is Policy.ASK

    @property
    def denied(self) -> bool:
        return self.policy is Policy.DENY


def declaration(name: str) -> Declaration:
    """The declaration for a tool. An undeclared name is a programming error."""
    try:
        return TOOL_DECLARATIONS[name]
    except KeyError:  # pragma: no cover - covered by the coverage test
        raise PolicyError(f"tool {name!r} has no declared class") from None


def parse_overrides(
    overrides: str | Mapping[str, str | Policy] | None,
) -> dict[str, Policy]:
    """`"send_message=deny,control=ask"` → `{"send_message": DENY, ...}`.

    A malformed entry raises rather than being skipped: a security knob that
    fails open is worse than no knob. A `Mapping` may carry either the raw
    strings (as `.env` provides) or already-parsed `Policy` values.
    """
    if overrides is None or overrides == "":
        return {}
    if isinstance(overrides, str):
        raw: dict[str, str | Policy] = {}
        for pair in (p.strip() for p in overrides.split(",")):
            if not pair:
                continue
            if "=" not in pair:
                raise PolicyError(f"override {pair!r} is not <name>=<allow|ask|deny>")
            key, _, value = pair.partition("=")
            if not key.strip():
                raise PolicyError(f"override {pair!r} has no name")
            raw[key.strip()] = value.strip()
    else:
        raw = dict(overrides)

    parsed: dict[str, Policy] = {}
    for key, value in raw.items():
        if isinstance(value, Policy):
            parsed[key] = value
            continue
        try:
            parsed[key] = Policy(value)
        except ValueError:
            raise PolicyError(
                f"override for {key!r} is {value!r}; expected one of "
                f"{', '.join(p.value for p in Policy)}"
            ) from None
    return parsed


def resolve(name: str, overrides: str | Mapping[str, str | Policy] | None = None) -> ToolDecision:
    """Most-specific-wins, with deny winning outright.

    Applicable rules are collected, not short-circuited: a class-wide `deny`
    applies to every tool in the class no matter what a per-tool `allow` says.
    """
    decl = declaration(name)
    rules = parse_overrides(overrides)

    class_override = rules.get(decl.cls.value)
    tool_override = rules.get(name)

    policies = [CLASS_DEFAULTS[decl.cls]]
    if class_override is not None:
        policies.append(class_override)
    if tool_override is not None:
        policies.append(tool_override)

    if Policy.DENY in policies:
        source = (
            "override:tool"
            if tool_override is Policy.DENY
            else "override:class"
            if class_override is Policy.DENY
            else "class-default"
        )
        return ToolDecision(
            tool=name,
            cls=decl.cls,
            policy=Policy.DENY,
            source=source,
            reason=f"{decl.cls.value} is denied for {name!r} ({source})",
        )

    if tool_override is not None:
        return ToolDecision(name, decl.cls, tool_override, "override:tool")
    if class_override is not None:
        return ToolDecision(name, decl.cls, class_override, "override:class")
    return ToolDecision(name, decl.cls, CLASS_DEFAULTS[decl.cls], "class-default")


def unknown_overrides(overrides: str | Mapping[str, str] | None) -> list[str]:
    """Override keys that name neither a tool nor a class.

    Reported rather than raised: a typo in `.env` must not stop Iris booting, but
    it must not be invisible either — `iris tools` prints this.
    """
    parsed = parse_overrides(overrides)
    classes = {c.value for c in ToolClass}
    return sorted(k for k in parsed if k not in TOOL_DECLARATIONS and k not in classes)


def policy_snapshot(overrides: str | Mapping[str, str | Policy] | None = None) -> list[dict]:
    """Every declared tool with its resolved policy — the CLI/API readout."""
    rows = []
    for name, decl in TOOL_DECLARATIONS.items():
        decision = resolve(name, overrides)
        rows.append(
            {
                "tool": name,
                "class": decl.cls.value,
                "policy": decision.policy.value,
                "source": decision.source,
                "surface": decl.surface,
            }
        )
    return rows


def surface_order(
    budget: int,
    *,
    promoted: Sequence[str] = (),
    present: Sequence[str] | None = None,
) -> tuple[list[str], list[str]]:
    """Split tools into `(visible, deferred)`.

    Deterministic by construction: core tools are never deferred, promotion wins
    over the budget, and among extended tools the *last declared* is deferred
    first, so the declaration order above is the priority order.

    `present` restricts the answer to the tools actually registered this boot —
    the Telegram tools exist only when a channel is connected, and `computer`
    only when computer-use is enabled, so a budget must be spent on what is
    really there. `None` means "every declared tool".

    `budget <= 0` means "no posting budget" (everything visible) rather than
    "hide everything", because the second is never what anyone means.
    """
    declared = set(present) if present is not None else set(TOOL_DECLARATIONS)
    promoted_set = set(promoted) & declared
    core = [n for n, d in TOOL_DECLARATIONS.items() if d.surface == "core" and n in declared]
    extended = [n for n, d in TOOL_DECLARATIONS.items() if d.surface == "extended" and n in declared]

    rest = [n for n in extended if n not in promoted_set]
    visible = list(core) + [n for n in extended if n in promoted_set]
    if budget <= 0:
        return visible + rest, []
    if len(visible) >= budget:
        return visible, rest

    room = budget - len(visible)
    return visible + rest[:room], rest[room:]
