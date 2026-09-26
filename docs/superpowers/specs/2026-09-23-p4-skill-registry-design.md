# P4 — Skill registry, manifests and the execution boundary

**Phase:** P4 of 8 · **Status:** owner-approved 2026-09-23 (four design answers)
**Plan:** `docs/superpowers/plans/2026-09-23-p4-skill-registry.md`
**Baseline:** P3 verified — 299 collected, 294 runnable, ruff clean.

## Goal

Turn `workspace/skills/` from "a directory of files the agent happens to read"
into a **registry**: skills are discovered from several sources, normalized into
one validated shape, selected under a policy, and — if they ship code — executed
inside an explicit boundary with a judgment gate in front of it.

Three properties matter more than features:

1. **Nothing is silently dropped.** Two skills with the same name are a reported
   conflict, not a coin flip.
2. **A skill cannot escalate.** It may only narrow what the agent is allowed to
   do; it can never grant a tool or a permission the runtime does not already have.
3. **Safe loading holds by construction.** Discovery reads *data*. The only way a
   skill's code runs is through one tool, and that tool refuses more than it allows.

## Owner decisions (2026-09-23)

| Question | Answer | Consequence |
|---|---|---|
| Skill model | **Extend the existing `Skill`** | One concept. Learned procedures gain manifest fields; there is no second registry, and the learning loop (`skill_write`, reinforce/revise, dreaming) keeps working unchanged. |
| Discovery | **Maximum coverage** | Three sources: repo builtins, the workspace, and installed packages via entry points — with explicit precedence and conflict reporting. |
| Skill code | **Safe *and* capable, following the most-used option, with JEV gating unsafe execution** | The widely-adopted Agent Skills format (`SKILL.md` + optional `scripts/`), plus a boundary that strips secrets, enforces a timeout and requires a judgment screen *and* owner approval before anything runs. |
| CLI | **`list` + `show` + `validate`** | Read-only; `validate` is the machine interface (non-zero exit on errors). |

## Industry basis (why this shape)

The shape is not invented here; it follows the format that Claude, OpenAI and
Microsoft converged on, plus the current security guidance:

- **The open Agent Skills specification** (<https://agentskills.io/specification>):
  a skill is a directory with `SKILL.md` — YAML frontmatter (`name`,
  `description`, optional `license`, `compatibility`, `metadata`,
  `allowed-tools`) plus a Markdown body, with optional `scripts/`, `references/`
  and `assets/` directories and **progressive disclosure** (metadata at startup,
  body on activation, resources on demand).
- **NVIDIA, *Practical Security Guidance for Sandboxing Agentic Workflows*** and
  the **OWASP AI Agent Security cheat sheet**: run untrusted work in isolation,
  start it with an empty/minimal credential set, scope tools by allowlist, and
  bound execution with timeouts.
- **Trail of Bits, *Prompt injection to RCE in AI agents* (2025)**: approval
  prompts alone are bypassable, so the boundary must hold even when the model is
  manipulated — which is why Iris's script path has a deterministic pre-screen, a
  minimal environment and a judgment gate *in addition to* approval, not instead
  of it.

Iris adapts the spec rather than copying it: `triggers` and `success_score` stay
(they are what the existing learning loop updates), and they ride in the
spec-sanctioned `metadata` map for standard-format skills.

## Data model

`Skill` (in `src/iris/memory/skills.py`) gains manifest fields. Every new field
has a default, so existing `workspace/skills/*.json` sidecars keep loading:

| Field | Default | Meaning |
|---|---|---|
| `version` | `""` | Author-declared version |
| `source` | `"learned"` | `learned` \| `workspace` \| `builtin` \| `package:<dist>` |
| `enabled` | `True` | A disabled skill is listed, never injected or selected |
| `allowed_tools` | `[]` | **Empty = no restriction** (back-compatible with learned skills). Non-empty = while this skill is active, only these tools may run |
| `timeout_seconds` | `10.0` | Upper bound for this skill's scripts, itself capped by config |
| `license`, `compatibility` | `""` | Passed through from frontmatter |
| `root` | `""` | Skill directory (standard layout); empty for flat learned skills |
| `scripts` | `[]` | Relative paths found under the skill's `scripts/` |
| `references` | `[]` | Relative paths found under `references/` |
| `metadata` | `{}` | The spec's arbitrary string map |

`triggers`, `description`, `procedure`, `success_score`, `created`, `updated`
are unchanged — this is the same object, widened.

### Two encodings, one concept

| Encoding | Where | Produced by |
|---|---|---|
| **Learned (existing)** | `workspace/skills/<name>.md` + `<name>.json` | `skill_write`, dreaming, reinforce/revise |
| **Standard (spec)** | `<root>/<name>/SKILL.md` (+ `scripts/`, `references/`, `assets/`) | shipped builtins, installed packages, owner hand-authoring |

Both normalize into `Skill`. A standard skill's triggers come from
`metadata.iris-triggers` (comma or space separated) so JEV selection and
`match_triggers` work identically for both encodings.

## Discovery and precedence

Sources, highest precedence first:

1. `workspace/skills/` — `source="learned"` for flat sidecar skills,
   `"workspace"` for `SKILL.md` directories the owner wrote there.
2. `skills/` in the repository root — `source="builtin"` (versioned, shipped).
3. Installed distributions advertising the `iris.skills` entry point, each
   naming a directory of skill packages — `source="package:<dist>"`.

Rules:

- **Precedence decides, and the loser is recorded** (`RegistryConflict` with
  winner, loser, both sources). `iris skills validate` prints conflicts; nothing
  is invisible.
- Ordering is deterministic (precedence, then name) so prompt content and traces
  are reproducible.
- A malformed skill never aborts discovery: it becomes a `ValidationIssue` and is
  excluded from the active roster.
- `enabled: false` (frontmatter or sidecar) removes a skill from selection and
  injection but keeps it visible to `iris skills list`.

## Policy

Selection is unchanged from P2/P3 (JEV suggestion with a confidence gate, or the
deterministic trigger matcher when JEV is unavailable). What P4 adds is
**enforcement**:

- The turn records its active skill (`active_skills` in graph state, set by the
  context node from the block it just injected).
- `dispatch()` consults the policy: when the active skill declares a non-empty
  `allowed_tools`, any tool call outside that union is refused with a message
  naming the skill, and the refusal is recorded in the turn trace.
- Tools named in a manifest must exist in the runtime's tool registry. An unknown
  name is a **validation error**, not a silent no-op — otherwise a typo becomes an
  invisible permission drop.
- A skill can never add a tool that `tool_schemas()`/`NON_OWNER_BLOCKED` already
  withholds for the session's origin: the policy intersects, it never unions.

## The execution boundary (scripts)

A skill's code runs only through one new tool, `skill_run(name, script, args)`:

1. **Resolution** — the script path must resolve *inside that skill's own
   `scripts/` directory*. Traversal, absolute paths, drive letters, backslashes
   and symlink escapes are rejected (same validators as `Sandbox`). Flat learned
   skills have no `scripts/`, hence nothing runnable.
2. **Deterministic pre-screen** (`pre_screen`) — scans the source for credential
   access, networking, process spawning, dynamic execution, and writes outside
   the skill directory. Any hit does not block by itself; it is *reported to the
   owner in the approval request*, so approval is informed rather than blind.
3. **Judgment gate** (`screen_script`) — one JEV noul: does this script do only
   what the skill's description says, without touching credentials, the network,
   or files outside its own directory? A score below the gate **blocks execution
   outright** (no approval can override it — a manipulated model asking nicely is
   exactly the case this exists for). JEV unavailable → the gate cannot pass
   silently; the run degrades to approval-only and records that it did.
4. **Owner approval** — always, via the existing `interrupt({"type": "approval"})`
   pattern, carrying the skill, the script, the pre-screen findings and the JEV
   verdict. `resume("approved")` is required to continue.
5. **Execution** — `asyncio.create_subprocess_exec` (argv, never a shell) with:
   - `cwd` = the skill directory,
   - an **environment built from scratch** (`PATH`, `LANG`, `TMPDIR`, `HOME`
     pointed at the sandbox) so no API key, `.env` entry or `IRIS_*` value is
     inherited,
   - a timeout (`min(manifest.timeout_seconds, config cap)`) after which the
     process is killed and reported as a timeout,
   - capped stdout/stderr (config), truncated with an explicit marker,
   - non-zero exit returned as data (`ok: false`) with the tail of stderr.
6. **Evidence** — every attempt (blocked, refused, timed out, succeeded) is a
   `turnlog` event, so "did a skill run?" is answerable from the trace.

**Residual risk, stated honestly:** this is process isolation, not kernel
isolation. A Python script running as the same OS user can still read files that
user can read and can still open a socket. That is why the JEV gate and the
approval are on the *path* to execution, the environment carries no secrets, and
scripts are opt-in per skill. Stronger isolation (container/namespace) is a
deployment concern, documented in `docs/deployment.md` rather than faked here.

## Configuration

| Setting | Default | Purpose |
|---|---|---|
| `skills_enabled` | `true` | Master switch (registry + injection) |
| `skills_builtin_dir` | `skills` | Repo-relative builtin root; `""` disables |
| `skills_extra_dirs` | `""` | Comma-separated extra roots (private skill packs) |
| `skill_script_timeout_seconds` | `10.0` | Hard cap over any manifest value |
| `skill_script_max_output_chars` | `20000` | stdout/stderr cap |
| `skill_script_require_approval` | `true` | Off = owner opted out (still JEV-gated) |
| `skill_guard_gate` | `0.60` | Minimum JEV safety score to allow a run (calibrated live: a safe stdlib script scored 0.72, a credential-exfiltrating variant 0.01 — the first draft's 0.70 left the safe case one quantum from refusal) |

## CLI

```
iris skills list                 # name, source, enabled, score, tools, scripts
iris skills show <name>          # manifest + full procedure
iris skills validate             # every issue + conflicts; exit 1 on errors
```

## Tests (all fake-driven, no network, no key, no database)

| File | Pins |
|---|---|
| `tests/test_skill_manifest.py` | Frontmatter parsing (name/description/license/metadata/allowed-tools), name-shape validation, `metadata.iris-triggers`, sidecar normalization, unknown-key warnings |
| `tests/test_skill_registry.py` | Three sources, precedence, conflict reporting, disabled handling, malformed skills excluded + reported, deterministic order |
| `tests/test_skill_policy.py` | Allowlist narrowing, empty allowlist = unrestricted, unknown tool = error, denial message + trace event, intersection with `NON_OWNER_BLOCKED` |
| `tests/test_skill_runner.py` | Path escape rejection, pre-screen findings, JEV-unsafe blocks without approval, JEV-absent → approval required, approval refusal, timeout kill, **environment stripped** (a script printing an inherited `API_KEY`/`IRIS_*` var prints nothing), output cap |
| `tests/test_skills_cli.py` | `list`/`show`/`validate` output and exit codes |

## Out of scope (reject during review)

- Marketplace, remote install, signing.
- Non-Python runtimes (the runner is argv-based; adding one is a config change,
  not a P4 deliverable).
- Kernel/container isolation of scripts.
- Any change to how skills are *learned* (dreaming, reinforce/revise).

## Deferrals (named, not forgotten)

- **Entry-point skill packages** land as directories of `SKILL.md` packages. If
  the roster grows past the JEV candidate cap, revisit the two-pass
  verify-then-select cookbook (`docs/jev.md`).
- **`iris skills enable/disable`** was offered and not chosen; the manifest field
  exists, so the command is additive later.
