# P4 plan — skill registry, manifests, execution boundary

**Spec:** `docs/superpowers/specs/2026-09-23-p4-skill-registry-design.md` (approved 2026-09-23)
**Rule:** TDD — write the failing test, run it, then implement. No commits unless the owner asks.
**Gate:** no P5 work until the owner ticks the P4 DoD.

## Global constraints

- No new runtime dependency beyond `pyyaml` (already in the lock via litellm; declared
  explicitly in `pyproject.toml` this phase because iris code now imports it).
- Everything fake-driven: no network, no provider keys, no Postgres in the suite.
- `uv run ruff check .` clean after every task; suite green after every task.
- Backward compatibility is a hard requirement: existing `workspace/skills/*.json`
  sidecars (with `svg-pro` / `create_svg_art` on disk today) must load untouched,
  and `tests/test_dreaming_skills.py` must stay green.

## Task 0 — Baseline

- [ ] Record `ruff` + suite state before touching anything.

## Task 1 — Manifest: parse and validate (TDD)

- [ ] **Step 1:** `tests/test_skill_manifest.py`: parse frontmatter from a `SKILL.md`
      string (name/description/license/compatibility/metadata/allowed-tools +
      `metadata.iris-triggers`), reject bad names (uppercase, leading/trailing hyphen,
      `--`, >64 chars), reject a missing description, warn on unknown keys, and
      normalize a legacy sidecar dict into the same `Skill`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Create `src/iris_ai/skills/` package: `manifest.py` with
      `parse_frontmatter()`, `skill_from_skill_md()`, `skill_from_sidecar()`,
      `validate_skill()`; widen `Skill` with the manifest fields (all defaulted).
- [ ] **Step 4:** Green + ruff + `tests/test_dreaming_skills.py` unchanged.

## Task 2 — Discovery and the registry (TDD)

- [ ] **Step 1:** `tests/test_skill_registry.py`: three sources discovered; precedence
      (workspace beats package beats builtin); a name clash is a recorded conflict with
      winner + loser + sources and is never silent; `enabled: false` hidden from
      selection but present in `list()`; malformed skills excluded *and* reported;
      deterministic ordering; a missing root is not an error.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** `src/iris_ai/skills/registry.py`: `SkillRegistry` (roots, entry-point
      discovery, precedence, conflicts, `list()`, `get()`, `validate()`, `reload()`).
      Wire it into `engine.py` where `SkillLibrary` is built, and keep
      `runtime.skills` pointing at the object the tools already use (no churn in
      `agent/tools.py` beyond the policy hook).
- [ ] **Step 4:** Green + ruff.

## Task 3 — Policy enforcement (TDD)

- [ ] **Step 1:** `tests/test_skill_policy.py`: non-empty `allowed_tools` narrows the
      turn to those tools; empty means no restriction; an unknown tool name is a
      validation error; a refusal names the skill and is recorded in the turn trace;
      the policy can only intersect, never add (`NON_OWNER_BLOCKED` still blocked).
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** `src/iris_ai/skills/policy.py` + `active_skills` in `IrisState`, set in
      `_assemble` from the context block, consumed by `dispatch(...)` in `agent/tools.py`
      and `_tools` in `agent/chat.py`. JEV suggestion unchanged.
- [ ] **Step 4:** Green + ruff.

## Task 4 — Script boundary (TDD)

- [ ] **Step 1:** `tests/test_skill_runner.py`: path escape (`..`, absolute, backslash)
      rejected; pre-screen flags credential/network/exec patterns; a JEV "unsafe"
      verdict blocks even with approval (no subprocess spawned — assert with a
      counting fake); JEV absent → approval still required and the trace says the
      gate did not run; approval refusal → no run; timeout kills and reports; **the
      child environment is stripped** (a script printing `os.environ` sees no
      `IRIS_*`/key vars); output is capped.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** `src/iris_ai/skills/runner.py` (`pre_screen`, `run_script`) +
      `src/iris_ai/skills/guard.py` (`screen_script` via `JevClient`) + the `skill_run`
      tool in `agent/tools.py` (approval interrupt, timeout, capped output, turnlog).
- [ ] **Step 4:** Green + ruff + a real (hermetic) subprocess smoke in the test suite:
      run a tiny stdlib-only script from a skill directory and assert stdout.

## Task 5 — CLI (TDD)

- [ ] **Step 1:** `tests/test_skills_cli.py`: `iris skills list` shows name/source/score/
      tools; `iris skills show <name>` prints the manifest and the procedure;
      `iris skills show` for an unknown name exits non-zero with a clear message;
      `iris skills validate` exits 1 when a skill is malformed and 0 when clean;
      the command registry grows to `{chat, doctor, skills, version}`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** `src/iris_ai/cli/skills.py` + register in `cli/main.py`; update
      `tests/test_cli.py`'s exact-registry assertion.
- [ ] **Step 4:** Green + ruff.

## Task 6 — Ship one builtin skill (evidence the standard format works)

- [ ] `skills/web-page-to-notes/SKILL.md` (spec frontmatter, `metadata.iris-triggers`,
      `allowed-tools`) + `skills/web-page-to-notes/scripts/extract.py` — stdlib only,
      reads a local HTML file from the sandbox and writes clean notes. No network.
- [ ] `tests/test_skill_registry.py` gains a case asserting the shipped builtin is
      discovered, parses, and validates clean.

## Task 7 — Docs, progress, DoD

- [ ] `README.md`: skills section (registry, sources, policy, boundary, CLI), status → P4.
- [ ] `docs/blueprint.md`: P4 marked shipped + deferrals.
- [ ] `CHANGELOG.md`: P4 section with real counts.
- [ ] `docs/jev.md`: the script gate is a JEV integration now — add it to the table.
- [ ] `docs/deployment.md`: the residual-risk paragraph (process isolation, not kernel).
- [ ] `docs/superpowers/progress/p4-execution.md`: tasks, verification log, DoD checklist.
- [ ] Run the DoD commands, hand the checklist to the owner, **stop before P5**.

## Out of scope (reject during review)

- Marketplace, remote install, signing.
- Non-Python runtimes, kernel isolation, container-per-script.
- Any change to skill *learning* (dreaming, reinforce/revise).
