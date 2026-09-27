# Handoff — how to continue Iris

**Read this first.** It is written for an agent starting a fresh session with no
memory of the work so far. Everything here was true at the commit named below;
re-verify rather than trust it.

---

## 1. Where things stand

- **Repo:** `omhome16/Iris` · branch **`refactor/modernize-jev`**. The last
  feature commit is **`68500f2`** ("feat(tools): make a deferred tool findable,
  and load it for good"); the vault-review doc fix (`6699b94`) and this handoff
  doc sit on top of it. Run `git log --oneline -1` rather than trusting either hash.
- **State:** working tree clean · `uv run ruff check .` → clean ·
  `uv run pytest tests -q --ignore=tests/test_memory_pipeline.py
  --ignore=tests/test_retrieval_gate.py` → **883 passed**; with Postgres up the
  full suite is **891 passed**. (A stale number here is worse than none —
  re-run it.)
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
uv run pytest tests -q --ignore=tests/test_memory_pipeline.py \
                        --ignore=tests/test_retrieval_gate.py   # fast, no DB
docker compose up -d postgres           # then the full suite (8 DB-backed tests)
uv run pytest tests -q                  # full suite
uv run iris version && uv run iris doctor
```

- The 5 tests in `tests/test_memory_pipeline.py` need pgvector on `:5433` and
  **fail loudly** when it is absent (they do not skip). `tests/test_retrieval_gate.py`
  is the same: it uses the `iris_eval` scratch database, and
  `tests/test_retrieval_metrics.py` is the DB-free half of that gate.
- **Both scratch databases are created on demand** (`tests/db.py`), so there is no
  `CREATE DATABASE` step — not locally and not in CI. They are the suite's own
  databases, deliberately separate from real memory; drop them freely, the next
  run recreates them.
- `docs/MERGE-AND-VERIFY.md` is the end-to-end runbook: merge, verify, reproduce
  the CI runner locally, and what each of the five checks proves.
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

Also done, and no longer in §4: hash-pinned third-party skill manifests (`e696b9c`),
the TTFT/TPOT split (`da48b3e`), the memory-OFF ablation baseline (`c6b48bd`),
the model-free retrieval gate in CI (`37f5592`), deferred tool loading with a
namespace catalog (`68500f2`), and the vault-review doc fix (`6699b94`).

Read `docs/vault-review.md` for the full map of what the vault recommends versus
what Iris already has — its backlog is now empty, recorded as a table of what
landed.

## 4. Remaining work — all of it is now done

Each item was implemented, verified against the suite and committed on its own.
`docs/vault-review.md` carries the same list with the vault's justification for
each one.

| Item | Commit |
|---|---|
| Hash-pin third-party skill packages, re-approve on change | `e696b9c` |
| TTFT/TPOT split, rather than total latency | `da48b3e` |
| Memory-OFF ablation baseline | `c6b48bd` |
| A model-free retrieval gate in CI | `37f5592` |
| Deferred tool loading / tool search past ~20 tools | `68500f2` |
| Doc fix — the kill switch leaves the vault-review backlog | `6699b94` |

Nothing here is outstanding. §5 is the user's publishing path, not an agent's.

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
