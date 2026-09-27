# P4 Execution Progress

**Plan:** `docs/superpowers/plans/2026-09-23-p4-skill-registry.md`
**Spec:** `docs/superpowers/specs/2026-09-23-p4-skill-registry-design.md` (approved 2026-09-23)
**Branch:** `refactor/modernize-jev`
**Started / completed:** 2026-09-23
**Rules:** No commits unless the owner explicitly asks. No P5 work until the owner
verifies this phase's DoD.

## Owner decisions (asked and answered before the work)

1. **Extend the existing `Skill`** — one concept, no second registry. The
   manifest fields are added to the dataclass `SkillLibrary` already writes.
2. **Maximum-coverage discovery** — every source, not just the workspace.
3. **Safe *and* capable code loading**, following the most-used industry option,
   with **JEV used to block unsafe execution** rather than only to advise.
4. **CLI stays read-only** — `list`, `show`, `validate`.

## Tasks

| Task | Description | Status | Evidence |
|------|-------------|--------|----------|
| 0 | Baseline (ruff + suite before touching anything) | done | `ruff` clean; 294 passed with the DB suite excluded (P3 exit state) |
| 1 | Manifest: parse + validate (TDD) | done | `src/iris_ai/skills/manifest.py` (`split_frontmatter`, `parse_skill_md`, `parse_sidecar`, `validate_skill`); `Skill` widened (all fields defaulted); `tests/test_skill_manifest.py` (24) written failing first |
| 2 | Discovery + registry (TDD) | done | `src/iris_ai/skills/registry.py` (`SkillRegistry`, `RegistryConflict`, `open_registry`); four sources in precedence order; conflicts reported; no caching; `engine.py` wired; `tests/test_skill_registry.py` (20) |
| 3 | Policy enforcement (TDD) | done | `src/iris_ai/skills/policy.py` (`SkillPolicy`, `policy_for`); `IrisState.active_skills`; `dispatch()` + `tool_schemas()` enforce/filter; denials in the turn trace; `tests/test_skill_policy.py` (15) |
| 4 | Script boundary (TDD) | done | `src/iris_ai/skills/runner.py` (AST `pre_screen`, `resolve_script`, `run_script`) + `guard.py` (`screen_script` via `JevClient`) + the `skill_run` tool; `tests/test_skill_runner.py` (31) and `tests/test_skill_tool.py` (13) |
| 5 | CLI (TDD) | done | `src/iris_ai/cli/skills.py` + `skills` command in `cli/main.py`; command registry now exactly `{chat, doctor, skills, version}` (assertion updated); `tests/test_skills_cli.py` (11) |
| 6 | Ship one builtin skill | done | `skills/web-page-to-notes/SKILL.md` + stdlib-only offline `scripts/extract.py`; two registry tests prove it is discovered, valid, and its script really runs |
| 7 | Docs, progress, DoD | **awaiting owner ticks** | README / CHANGELOG / blueprint / `docs/jev.md` / `docs/deployment.md` updated; this log; evidence below |

## Verification log

All commands from the repo root, 2026-09-24.

| Command | Result |
|---------|--------|
| `uv run ruff check .` | `All checks passed!` |
| `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py` | `410 passed, 1 warning` (294 → 410: the P4 suites below plus fixtures moved to the registry) |
| `uv run pytest tests --collect-only -q` | `415 tests collected` (410 runnable here + the 5 pgvector tests) |
| `uv run iris --help` | commands are exactly `chat`, `skills`, `version`, `doctor` |
| `uv run iris skills validate` | `1 warning(s) · 0 error(s)` — the `create_svg_art` name-convention warning, no errors; exits 0 |
| `uv run iris skills list` (owner's real workspace) | `create_svg_art` (learned, 0.70), `svg-pro` (learned, 0.60), `web-page-to-notes` (builtin, 1 script) |
| `uv run iris doctor` | `TYPESAFE_API_KEY: set` (name only; the doctor never prints values) |
| Live JEV gate — the shipped `web-page-to-notes` script | score **0.720 → allowed** (gate 0.60), judged live with the real key, not mocked |
| Live JEV gate — a credential-exfiltrating variant | reads `os.environ['TYPESAFE_API_KEY']` and `urllib`-POSTs to `evil.example` → score **0.010 → refused**, no subprocess spawned |

## DoD checklist (owner must tick)

- [x] One `Skill`, with manifests; the flat sidecar pair and the Agent Skills
      `SKILL.md` layout normalize into the same concept
- [x] `SkillRegistry` discovers all sources in precedence order and reports a
      name clash (winner + loser + path) instead of shadowing silently
- [x] Malformed skills are excluded **and** reported; `iris skills validate`
      exits 1 on error-level issues
- [x] `allowed-tools` is enforced at the single dispatch choke point and can only
      narrow — never widen — the session rule
- [x] Code runs through exactly one path (`skill_run`) with in-directory
      resolution, an AST pre-screen, a **binding** JEV gate, owner approval, a
      constructed environment, a timeout and capped output
- [x] Pre-P4 skills load unchanged (no sidecar is rewritten); the learning loop
      (dreaming, `skill_write`, reinforce/revise) is untouched
- [x] One builtin skill ships the standard format end to end, with a test that
      runs its script for real
- [x] `uv run ruff check .` clean
- [x] `uv run pytest -q` green apart from the 5 pgvector tests (CI runs all)
- [ ] **Owner confirmation** — the gate for P5

## Deviations from the plan (with reasons)

1. **The identity errors raise; everything else is a graded issue.** The plan
   said "reject bad names / missing description". Making *all* validation
   fatal would have made the already-on-disk `create_svg_art` (and the prose
   names the agent invents mid-conversation, e.g. `"Draft Standup"`) unloadable.
   A missing name or a path-unsafe name raises; a name that only violates the
   *lowercase-hyphen convention* is a warning, and the skill still loads. That
   keeps `tests/test_agent_graph.py::test_skill_use_reinforces_success_score`
   green without weakening the parts that actually matter for safety.
2. **`open_registry(files, known_tools=…)` instead of the plan's module-global
   registry.** The plan sketched a registry built at import; discovery is now an
   explicit factory that takes the workspace `files` and the real `TOOL_NAMES`,
   so tests drive it with fakes and validation is checked against the actual
   tool surface rather than a copy.
3. **Writes stay in `SkillLibrary` and refuse non-`learned` sources.** The plan
   left it open. Letting the registry write would have implied a second writer
   and the ability to "edit" a shipped skill by writing a shadow copy. It now
   raises `PermissionError("… only learned skills carry a success score …")`.
4. **`assemble_turn()` returns `(text, named)`.** The policy needs to know which
   skills the prompt *named*, and naming a skill is what activates it. Returning
   the set from the assembler (and storing it in `IrisState.active_skills`) was
   smaller than re-deriving the selection later, and it keeps the prompt side and
   the policy side looking at the same source.
5. **`asyncio.create_subprocess_exec` → a worker thread around `subprocess.run`.**
   The full suite caught it: `create_subprocess_exec` is unimplemented on the
   Windows `Selector` event loop that `iris_ai.api` selects for psycopg, so a skill
   script would have failed in the API process. The runner now runs the child on
   a thread, which works on both loops.

## Notes and follow-ups

- **Not verified locally:** the 5 `test_memory_pipeline.py` tests still need
  Postgres. Docker's daemon was unresponsive on this machine during the pass
  (`docker version` → `rc=124`), so they remain CI-verified — the same gap P1,
  P2 and P3 recorded.
- **The JEV gate was recalibrated in this phase.** `skill_guard_gate` moved from
  the initial 0.70 to **0.60** after measuring the live verdicts above (0.720 for
  the honest script, 0.010 for the exfiltration variant): 0.70 sat close enough
  to the honest script's score that a small wording change could have flipped it
  to a refusal. The spec's gate table carries the calibration note.
- **Residual risk documented, not fixed:** script execution is process
  isolation, not kernel isolation — an approved script runs as the same OS user
  as Iris. See `docs/deployment.md` → "Skill scripts". Container-per-script was
  explicitly out of scope for this phase.
- **Closed after P4 (owner-selected cleanup, 2026-09-24):** the dead recall-scoring
  knobs were deleted from `src/iris_ai/config.py`. `hybrid_top_k: int = 20` was the
  one already on the books from P2; auditing the same block turned up a second,
  `mrr_top_k: int = 5`, equally unread. Both were superseded by explicit per-call
  arguments — `MemoryIndex.search` / `escalate` take `top_k` / `mrr_top_k`, and
  every real caller passes literals (`agent/tools.py:93,95,464`, `api.py:488`).
  The block is now `recency_half_life_days` alone (the only live field;
  `memory/forgetting.py:35` reads it), renamed "Recall decay" with a comment
  recording why the top-k knobs were deleted rather than wired in: one global
  default cannot size the agent tool, the escalation lane and the research
  subagent at once. No behavior change — nothing read them.
- **No commits made** — repo rule.
