# Handoff — how to continue Iris

**Read this first.** It is written for an agent starting a fresh session with no
memory of the work so far. Everything here was true at the commit named below;
re-verify rather than trust it.

---

## 1. Where things stand

- **Repo:** `omhome16/Iris` · branch **`refactor/modernize-jev`**. The last
  feature commit is **`60241d6`** ("feat(harness): a kill switch, and best-so-far
  instead of an apology"); this handoff doc is a commit on top of it. Run
  `git log --oneline -1` rather than trusting either hash.
- **State:** working tree clean · `uv run pytest tests -q` → **831 passed** ·
  `uv run ruff check .` → clean.
- **Package identity (do not change without reason):** distribution
  `iris-personal-ai`, import package `iris_ai`, console command `iris`.
  `iris` was renamed because the PyPI name is taken and the `iris` import belongs
  to SciTools Iris.
- **Not published.** No tag, no PyPI upload. The user will verify personally
  before publishing, and wants the code work finished first.

Do not run `git push --force`, do not publish, and do not rewrite pushed history
without an explicit instruction. Publishing steps are in §5 and are the user's to run.

## 2. How to work in this repo

```bash
uv run ruff check .                     # must be clean before every commit
uv run pytest tests -q --ignore=tests/test_memory_pipeline.py   # fast, no DB
docker compose up -d postgres           # then the full suite (5 DB-backed tests)
uv run pytest tests -q                  # full suite
uv run iris version && uv run iris doctor
```

- The 5 tests in `tests/test_memory_pipeline.py` need pgvector on `:5433` and
  **fail loudly** when it is absent (they do not skip).
- Never print a secret value. `iris doctor` prints presence only, deliberately.
- Commit convention: `type(scope): imperative summary`, a body that explains
  **why**, ending with the Codebuff footer used by every commit on this branch.
- **Verify before claiming.** Run the suite and paste the real number. If a test
  is red, say so rather than describing the intent.

## 3. Already done — do not redo

Rebirth P1–P8; hardening pass (day-budget bug fixed, cached-token source, JEV
reflection + reply-path timeout); rename to `iris-personal-ai`/`iris_ai`; the
provider registry (12 providers + any OpenAI-compatible endpoint) with doctor
wiring; publishing metadata, LICENSE, version 0.2.0, wheel shipping the builtin
skill; the release workflow; the README rewrite; **prompt identity on every
trace** (`PROMPT_VERSION` + assembled-prefix fingerprint); and the **kill switch
+ best-so-far**.

Read `docs/vault-review.md` for the full map of what the vault recommends versus
what Iris already has.

## 4. Remaining work, with the seam for each

Ordered by value. One commit each, verified, is the pattern used so far.

### 4.1 Hash-pin third-party skill packages, re-approve on change
**Why:** the vault's MCP-security case — "a previously trusted tool starts
behaving differently after an update" (rug pull). A `package:` skill is an
installed distribution and currently trusted unconditionally.
**Seam:** `SkillRegistry._resolve()` → its inner `collect()` in
`src/iris_ai/skills/registry.py`, where a manifest's text is read and parsed.
**Shape:** digest the raw manifest text for `package:` (and arguably `extra`)
sources; keep an approval store at `workspace/config/skill_approvals.json`
mapping `source/name → sha256`; first sighting records (trust on first use) and
a *changed* digest raises a `ValidationIssue("error", …)` that disables the skill
until re-approved. Add `iris skills approve <name>` (see
`src/iris_ai/cli/skills.py`) and a test that a mutated manifest is refused.

### 4.2 TTFT / TPOT split, rather than total latency
**Why:** "only total latency logged" is a listed observability failure; a p99
cannot be attributed to prefill or generation without the split.
**Seam:** the streamed path (`respond_stream`) in `src/iris_ai/agent/chat.py`,
plus `turnlog.mark(stage, ms)` in `src/iris_ai/turnlog.py`.
**Shape:** record `ttft` (first token) and derive tokens/second from the final
usage, as `stages_ms` entries, on the streamed path only (the buffered path has
no first token).

### 4.3 Memory-OFF ablation baseline
**Why:** "memory was never evaluated with an OFF baseline so nobody noticed it
hurt." The current study predates the JEV-era capture node.
**Seam:** `scripts/eval_lab.py`, output `reports/eval_lab.md`.
**Shape:** add a memory-disabled arm and report the delta against it with the
existing Wilson intervals and the pre-registered decision rule in
`src/iris_ai/eval/stats.py`.

### 4.4 A model-free retrieval gate in CI
**Why:** two-speed gating — deterministic retrieval metrics on every PR, judge
suites later.
**Seam:** a labelled fixture (query → relevant chunk ids) plus a test that
computes `recall@k` / `nDCG@k` with no model. Wire it into
`.github/workflows/ci.yml` next to the existing lint/test jobs.
**Note:** the runtime already logs the rerank candidate pool and each score
(`src/iris_ai/memory/index.py`), so the run-time half exists; this adds the
labelled half.

### 4.5 Deferred tool loading / tool search past ~20 tools
**Why:** the vault's "≤ ~30 tools per agent, deferral beyond that", and the
catalog is a recurring per-turn token cost.
**Seam:** `src/iris_ai/toolpolicy.py` (declarations and classes),
`src/iris_ai/agent/context.py` (what enters the prompt), `src/iris_ai/cli/tools.py`
(the visible-surface readout).
**Shape:** expose namespace-level descriptions plus a search/load tool for the
deferrable remainder, appending loaded subsets at the **tail** so the cached
prefix is preserved. Largest item here; do it last.

### 4.6 Doc fix
`docs/vault-review.md` still lists the kill switch in its backlog. Move it to
"already have" when convenient.

## 5. Publishing — the user's steps, not yours

Do not run these; the user will. Verify only.

1. Confirm the two choices made on their behalf: **MIT license**, **version
   0.2.0** (`src/iris_ai/__init__.py`, `LICENSE`).
2. Clean-clone check: `uv sync`, ruff, both test runs, `uv build`, then install
   the wheel in a fresh venv and run `iris version` / `iris doctor` /
   `iris skills list` (the last one proves the packaged builtin resolves).
3. Merge `refactor/modernize-jev` into `main`.
4. Add a **trusted publisher** on PyPI and TestPyPI: repo `omhome16/Iris`,
   workflow `release.yml`, environments `pypi` and `testpypi`. No token is used
   — the workflow uses OIDC.
5. `git tag v0.2.0 && git push origin v0.2.0` → builds, installs and runs the
   artifact, publishes to TestPyPI, then PyPI.
6. `docker build -t iris:local . && docker run --rm iris:local id` should print
   `uid=10001(iris)`; the changelog claim rests on an older build and has not
   been re-verified.

## 6. Known blemishes, deliberately not fixed

- The `dashboard/` removal sits inside the **first** commit of the publish pass
  (`docs: blueprint and phase specs…`) rather than its own `chore:` commit.
  Fixing it means rewriting pushed history — needs explicit permission.
- `learning doc/` is untracked but was text-updated for the rename; harmless.
- Skill scripts use process isolation, not a kernel boundary. That is stated
  plainly in `docs/deployment.md` and is a deliberate decision, not an oversight.
