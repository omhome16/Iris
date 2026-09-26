# P7 Execution Progress

**Plan (design + tasks):** `docs/superpowers/plans/2026-09-24-p7-computer-use.md`
**Audit:** `docs/superpowers/specs/2026-09-24-principles-conformance-audit.md` (gaps: tool classes undeclared, tool surface over the soft limit, no kernel-boundary story)
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-25

## Gate status

P4's DoD is still unticked and P5's checklist is open. The owner gave a standing
instruction on 2026-09-24 to finish every phase; that waiver is recorded in
`p5-execution.md` and applies here. No checklist is retro-ticked.

**Docker:** out of bounds by owner instruction. The five `test_memory_pipeline.py`
tests stay CI-verified. **No commits.**

**A note on process:** the phase began with the audit the owner asked for — Iris
checked against their AI-Mastery vault — and the audit's verdict was that P7 is
the phase where two latent weaknesses stop being latent. So the implementation is
built around three decisions (declared classes, one-tool surface strategy, a
permission model that is the boundary) rather than around a browser driver.

## What already existed (and was therefore not rebuilt)

`src/iris_ai/agent/tools.py` owned a hand-written `NON_OWNER_BLOCKED` set and
`src/iris_ai/agents/roles.py` a second `READ_ONLY_TOOLS` set. Both were allowlists
with no default: adding a tool kept it classified only if someone remembered to
edit them. The audit named this shape, and P7 replaced it with declarations plus a
coverage test rather than adding a third list.

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline | done | P6 exit state: ruff clean, 639 passed at P7 start |
| 1 | `src/iris_ai/toolpolicy.py` — classes, class→policy, most-specific-wins, **deny wins**, overrides (TDD) | done | `TOOL_DECLARATIONS`, `CLASS_DEFAULTS`, `resolve`, `policy_snapshot`; `tests/test_tool_policy.py` |
| 2 | Wire the policy into `tool_schemas` (hide deny) and `dispatch` (refuse deny, record ask) | done | `_apply_tool_policy`, dispatch's class check; turn-trace events `tool_denied` / `tool_needs_approval` |
| 3 | `src/iris_ai/computer/` — vocabulary + provider boundary (TDD) | done | `actions.py`, `provider.py`; `tests/test_computer_provider.py` |
| 4 | Permission model — suffix allowlists, destructive confirmation, grant + budget (TDD) | done | `permissions.py`; `tests/test_computer_permissions.py` |
| 5 | Action audit log — append-only, redacted, typed text as length+digest (TDD) | done | `audit.py`; `tests/test_computer_audit.py` |
| 6 | The `computer` tool (one tool, `action` enum) + class declaration + approval envelope (TDD) | done | `session.py` `Computer.execute`, tool in `agent/tools.py`, wiring in `engine.py`; `tests/test_computer_tool.py` |
| 7 | Surface budget + `find_tools` (TDD) | done | `surface_order`, `find_tools`; `tests/test_tool_policy.py` |
| 8 | Observability — `iris tools`, `GET /tools`, `GET /actions` (TDD) | done | `src/iris_ai/cli/tools.py`, routes in `api.py`; `tests/test_tools_cli.py` |
| 9 | Docs, progress, DoD | done | README / CHANGELOG / blueprint updated; this log |

## Verification log

All commands from the repo root, 2026-09-25.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | **`684 passed, 1 warning`** (639 → 684: +45 across tool policy, provider, permissions, audit, tool and CLI/route tests) |
| `uv run pytest tests/test_computer_provider.py tests/test_computer_permissions.py tests/test_computer_audit.py tests/test_computer_tool.py -q` | `34 passed` |
| `uv run pytest tests/test_tool_policy.py -q` | `43 passed` |
| `uv run iris --help` | commands are `agents`, `chat`, `cron`, `doctor`, `skills`, `tools`, `version` |

The two tests that had to change are the command-set assertions
(`tests/test_cli.py`, `tests/test_skills_cli.py`) — the phase *intends* to add the
`tools` command, so the exact-set assertion now includes it.

## DoD checklist (owner must tick)

- [x] Every tool declares a class, and the cover is asserted in **both** directions
      (no unclassified tool; no declaration for a tool that does not exist)
- [x] `deny` beats every override, including a class-wide deny that a per-tool
      `allow` attempts to re-open
- [x] Defaults preserve shipped behaviour — no existing tool changed policy
- [x] A malformed override raises at boot; an unknown override *name* is reported
      by `iris tools` and by `GET /tools` rather than being silently dropped
- [x] The visible-surface budget never hides a `core` tool at any budget down to
      zero, and deferral is deterministic (last-declared first)
- [x] A deferred tool is still callable and `find_tools` restores its schema
- [x] `example.com` does **not** match `evil-example.com`
- [x] The destructive subset always confirms, even inside a live grant
- [x] One approval buys a bounded number of actions; the grant expires by count
- [x] A typed secret never appears in the action log or the approval payload
- [x] Every attempted action produces exactly one audit record
- [x] A missing driver is an `unavailable` refusal, never an `ImportError` mid-turn
- [x] `computer_enabled=false` means the `computer` tool is **not registered**
- [x] `iris tools`, `GET /tools` and `GET /actions` report the same declarations
      the engine enforces
- [ ] Owner confirmation

## Deviations, and why

1. **`NON_OWNER_BLOCKED` and `READ_ONLY_TOOLS` are still present.** The plan said
   the declarations would replace them. The session rule (`origin != "owner"`) and
   the *class* rule are different questions — session scope is not a class — so the
   `NON_OWNER_BLOCKED` set stays as the session gate and the class table governs
   capability. `READ_ONLY_TOOLS` likewise stays as the research/critic role
   allowlist, which is a *narrowing* of the surface rather than a class. Both are
   now covered against the same `TOOL_NAMES` set, so they cannot drift into naming
   a tool that does not exist.
2. **The `computer` tool is registered conditionally, not declared as a stub.** Off
   means absent. A registered-but-refusing tool still tells the model a screen is
   one call away, which is exactly the misreading the audit warns about.
3. **Playwright is not a dependency.** It is imported lazily inside
   `availability()`; every install without the optional extra gets a stable
   `unavailable` reason instead of an import error. `NullProvider` is the default.
4. **One browser context per action.** Slower than a persistent browser, and the
   right trade for P7: a persistent browser keeps cookies and sessions alive across
   turns, which the permission model would then have to reason about. The
   per-action lifecycle means "the grant expired" also ends the session.
5. **The kernel-boundary story is a policy statement, not a hypervisor.**
   `computer_allow_owner_scripts_only` (default true) states the restriction; the
   Wasm/microVM tier is named as the prerequisite for lifting it.
6. **No separate P7 design spec.** The plan doubles as the design; the design is
   the decision list plus the config table, and a second document would only add a
   place for the two to disagree.

## Findings

1. **Two hand-maintained allowlists had no default** — adding a tool meant
   remembering to edit `NON_OWNER_BLOCKED` and `READ_ONLY_TOOLS`, and forgetting
   was silent. A both-directions coverage test makes it a red test.
2. **`TOOL_NAMES` was 23, over the vault's ≤20 visible line**, before P7 added
   anything. The surface budget + `find_tools` is the answer, and `computer` is
   declared `extended` so it defers rather than permanently raising the count.
3. **Nothing recorded what a screen action did.** P7 makes the audit log the single
   place an action is recorded, and routes every attempt — including refusals —
   through it.

## Notes and follow-ups

- **Deliberately still open, tracked in the blueprint:** P5.1 harness hardening
  (G1 loop/spiral detection, G2 cascade breaker, G3 budget scopes, G4 approval
  digest + replay guard) folded into P8, and P8's eval statistics (G6). The
  computer-use pre-tool guard order — allowlist → confirmation → budget → perform →
  record — is the shape G1/G2 will slot into.
- **Docker remains out of bounds**, so `iris[computer]` + `playwright install
  chromium` and the five Postgres tests are CI-verified only.
- **No live driver was exercised in this environment.** Provider tests cover the
  unavailable path and a fake provider; the Playwright paths are written
  defensively but verified by review, not by a running browser.
