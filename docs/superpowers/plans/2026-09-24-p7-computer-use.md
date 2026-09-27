# P7 — Computer-use: plan and design

**Phase:** P7 of 8 ([`docs/blueprint.md`](../../blueprint.md))
**Driven by:** [`specs/2026-09-24-principles-conformance-audit.md`](../specs/2026-09-24-principles-conformance-audit.md)
(gaps: tool classes undeclared, tool surface over the soft limit, no
kernel-boundary story)
**Baseline:** P6 exit — ruff clean, **596 passed**
**Docker:** out of bounds by owner instruction. **No commits.**

## The design, in one page

P7 is the phase where Iris gains the ability to *act on a screen*, and the audit
says that is exactly when two latent weaknesses stop being latent. So the phase
is built around three decisions rather than around a driver.

### Decision 1 — tools declare a class, and the class derives policy

Today `NON_OWNER_BLOCKED` is a hand-written set of four names, and
`READ_ONLY_TOOLS` is a second hand-written set. Adding a tool means remembering
to edit both. That is a hand-maintained allowlist with no default, which is the
shape the vault warns about.

From P7, every tool declares a **class** — `read`, `filesystem`, `memory_write`,
`network`, `credentialed`, `delivery`, `control` — and the class yields a
**default policy**: `allow` / `ask` / `deny`. Resolution is
**most-specific-wins** (per-tool override → class override → class default) and
**deny always wins** regardless of specificity. A declaration test asserts the
class table covers `TOOL_NAMES` exactly, in both directions, so a new tool cannot
arrive unclassified.

Defaults preserve shipped behaviour on purpose. Iris is a single-owner personal
assistant; the audit records that as a deliberate, documented deviation from
fail-closed-for-everything. What the classes change is that the two highest-risk
classes — `credentialed` (empty today, declared for the future) and `control`
(this phase) — default to **ask**, and an owner can move any tool to `deny` in
config without a code change.

### Decision 2 — new capability arrives as a namespace, not as five tools

The vault's surface guidance: ≤20 tools visible is comfortable, namespaces are
the answer from 20–100, deferred loading past 50. Iris is at **23** and P7 is
about to add desktop control. So P7 adds **one** tool, `computer`, whose first
argument is an `action` enum — screenshot, click, type, navigate — instead of
five tools. The surface strategy is therefore demonstrated by construction
rather than asserted in a doc.

That alone does not reduce the count, so the same task adds the mechanism the
strategy needs: a **visible budget** (`tool_surface_budget`, default 20). Each
tool is declared `surface="core"` (never deferred) or `surface="extended"`
(deferrable). When the visible set exceeds the budget, `extended` tools are
deferred **in declaration order** — a stable, testable rule — and a new
`find_tools` tool returns the schemas of deferred tools that match a query.

Three properties make deferral safe rather than lossy:

1. **Deferral is not permission.** A deferred tool can still be called by name;
   deferral only changes what is put in front of the model. It is a context
   decision, and it is recorded as one.
2. **A core tool is never deferred.** The budget can never hide the memory,
   file or search tools, and a test asserts it across every budget value down to
   zero.
3. **The budget cannot silently grow the prompt.** Adding a tool to the
   `extended` set is what makes the count go up; if the budget binds, the
   `find_tools` path is how the model reaches it.

### Decision 3 — a screen action is a privileged action, so it is gated three ways

`control` defaults to `ask`, so every computer-use action surfaces the existing
approval interrupt. On top of that:

- an **allowlist** of navigation targets (host suffixes) and app/window titles,
  matched by suffix so `*.example.com` is not matched by `evil-example.com`;
- **confirmation for the destructive subset** — a click, a submit, a keystroke
  into a credentialed-looking field — which is exactly the class of action the
  vault's HITL note says fails when a single approval is treated as consent for
  a whole sequence;
- a **per-session grant** with an action budget so one approval cannot authorize
  an unbounded loop of clicks.

Every action appends to an **audit log** (`config/actions.jsonl`) that goes
through P6's redaction and records what/when/where/outcome — never a typed
secret. Typed text is recorded as a hash and a length, not as text: an action log
that contains what was typed is a keylogger.

### Decision 4 — the kernel boundary is stated, not implied

P4 accepted process isolation for **owner-authored** skill scripts and documented
it as residual risk. The audit notes the vault's stronger position: a container
is not a containment boundary for untrusted or agent-generated code. P7 resolves
this the only honest way available without a hypervisor: the permission model is
**owner-authored-only by policy**, stated in config, in the docs and in the
refusal message, with the Wasm/microVM path named as the prerequisite for
lifting it. No new dependency, and no implication that `subprocess` is a sandbox.

## Tasks

Each task lands with its tests in the same commit-sized unit. TDD ordering: the
test file is named so a reviewer can check the claim.

| # | Task | Test file | Acceptance |
|---|---|---|---|
| 0 | Baseline | — | 596 passed, ruff clean |
| 1 | `src/iris_ai/toolpolicy.py` — tool classes, class→policy defaults, most-specific-wins, **deny wins**, config overrides, `policy_report()` | `tests/test_tool_policy.py` | a tool cannot be unclassified (both directions); `deny` beats every override; defaults reproduce today's behaviour for all 23 shipped tools |
| 2 | Wire the policy into `tool_schemas` (hide `deny`) and `dispatch` (refuse `deny`, route `ask` through the existing approval interrupt), recorded in the turn trace | `tests/test_tool_policy.py` | a denied tool is neither offered nor callable; an `ask` action records a denial reason when refused |
| 3 | `src/iris_ai/computer/` — `Action`/`Observation`, `ComputerProvider` protocol, `NullProvider`, lazily-imported `PlaywrightProvider` reporting a stable `unavailable` reason, `provider_from_settings()` | `tests/test_computer_provider.py` | an uninstalled driver is an `unavailable` refusal, never an `ImportError` mid-turn; a provider never raises into the graph |
| 4 | Permission model — target allowlists (suffix-matched), destructive-subset confirmation, per-session grant + action budget | `tests/test_computer_permissions.py` | `evil-example.com` is not matched by `*.example.com`; a grant expires by count; the destructive subset always confirms |
| 5 | Audit log — append-only `config/actions.jsonl`, redacted, typed text as hash+length | `tests/test_computer_audit.py` | a typed secret never appears in the log; every action produces exactly one record |
| 6 | The `computer` tool (one tool, `action` enum) + tool-class declaration + approval envelope | `tests/test_computer_tool.py` | ONE new name in `TOOL_NAMES`, `control` class, `ask` by default, safe refusal when no provider |
| 7 | Surface budget + `find_tools` | `tests/test_tool_surface.py` | core tools never deferred at any budget; deterministic deferral order; `find_tools` returns only deferred, matching schemas; a deferred tool is still callable |
| 8 | Observability — `iris tools`, `GET /tools`, `GET /actions` | `tests/test_tools_cli.py` | the CLI shows class + policy + deferral per tool; the routes report the same |
| 9 | Docs + `progress/p7-execution.md` + DoD | — | README/CHANGELOG/blueprint; the danger story written down |

## Config added (P7)

```python
# Tool classes and policy
tool_policy_overrides: str = ""       # e.g. "send_message=deny,control=ask"
tool_surface_budget: int = 20         # visible schemas; extended tools defer past it

# Computer-use
computer_enabled: bool = False        # opt-in; nothing is reachable until it is on
computer_provider: str = "null"       # "null" | "playwright"
computer_allowed_hosts: str = ""      # comma-separated suffixes, e.g. "example.com,*.docs.dev"
computer_allowed_apps: str = ""       # comma-separated window-title suffixes
computer_max_actions: int = 12        # per grant
computer_action_timeout_seconds: float = 15.0
computer_confirm_destructive: bool = True
computer_allow_owner_scripts_only: bool = True  # the kernel-boundary statement
```

## Out of scope (stated, not forgotten)

- Unattended production RPA, credential vaults, and any driver that logs into an
  account on Iris's behalf.
- A Wasm/microVM isolation tier: named as the prerequisite for third-party
  computer-use code, not built here.
- OpenTelemetry export (P5.1/P8 concern; the span fields are what matter).
