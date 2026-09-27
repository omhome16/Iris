# Merge and verify — the last mile

One document for the end of the work: get the branch into `main`, then prove the
result rather than assert it.

Every command here has been run against this branch, and every number is measured
rather than estimated. Where a step is here for a reason that is not obvious, the
reason is written down — a runbook that says "run the tests" without saying what
"passing" looks like is how a green check gets mistaken for a working system.

**Read §1 first.** There is one thing about this merge that is not a
fast-forward, and knowing it saves an hour.

---

## 0. The short version

If you already know the repo and just want the commands:

```bash
# 1. merge (see §3 for the PR route, which is the recommended one)
git checkout main
git pull --ff-only origin main
git merge --no-ff refactor/modernize-jev
git push origin main

# 2. verify
uv sync
uv run ruff check .                                             # clean
docker compose up -d postgres                                   # pgvector on :5433
uv run pytest tests -q                                          # 891 passed
uv build                                                        # then install it: §4.5
docker build -t iris:local . && docker run --rm iris:local id   # uid=10001
```

Everything below is the same thing with the reasoning, the expected output, and
what to do when a step fails.

---

## 1. What you are merging, and the one non-obvious fact

| | |
|---|---|
| Branch | `refactor/modernize-jev` |
| PR | **#2** (`refactor/modernize-jev` → `main`) |
| Base | `main` |
| Contents | The vault backlog (hash-pinned skill manifests, TTFT/TPOT, the memory-OFF ablation baseline, the model-free retrieval gate, deferred tool loading), the handoff/vault-review doc updates, and the CI fixes in §5 |

**It is not a fast-forward, and the reason is worth knowing:** `main` already
contains an *earlier* merge of this same branch (`2dc19b6 Merge pull request #1`)
plus five commits of its own. So both sides have commits the other lacks, and the
merge is a real merge commit either way. It is **clean — no conflicts** (checked
with `git merge-tree`, and the merged content is byte-identical to the branch for
the files in question). No history rewriting is needed, and none is wanted.

Two `Dockerfile` bugs also lived on `main` and are fixed on this branch (§5.3).
That means `main`'s `build` check has been red since `f22157c` — you are fixing
it, not inheriting it.

---

## 2. Before you start

| Requirement | Check | Notes |
|---|---|---|
| `git` | `git --version` | Any 2.38+ for `git merge-tree` |
| `uv` | `uv --version` | The repo's toolchain; `uv sync` builds the venv |
| Python | 3.12 or 3.13 | `requires-python = ">=3.12"` |
| Docker | `docker info` | **Docker Desktop must be running**, not just installed. `docker compose up -d postgres` fails with "cannot find the file specified" when the daemon is down |
| Port 5433 free | `docker ps` | The pgvector container publishes `5433:5432` |

**On Windows** (this repo is developed there): the commands below are written for
bash — use Git Bash. Two Windows-specific traps that cost real time:

- Docker receives a mangled path if a bind mount uses `-v host:/container`. Use
  `--mount type=bind,source=...` and, if a path starts with `/`, prefix the whole
  command with `MSYS_NO_PATHCONV=1`. (This is how a "fresh database" test silently
  ran against no init script at all.)
- Line-ending warnings (`LF will be replaced by CRLF`) during `git add` are
  expected and harmless on this branch.

Nothing in the verification writes to your real memory **except §4.8**, which is
called out there.

---

## 3. Merge

### Route A — the PR (recommended)

1. Open **PR #2** on GitHub.
2. Wait for all five checks to be green (`lint`, `retrieval`, `package`, `test`,
   `build` — see §5 for what each one proves).
3. Confirm GitHub still says "No conflicts with base branch".
4. **Merge pull request** → *Create a merge commit*. Do not squash: the branch's
   commits are the record of why each change exists, and the phase logs reference
   them by hash.
5. Then verify the merged `main` locally with §4.

### Route B — locally

```bash
git fetch origin
git checkout main
git pull --ff-only origin main           # main may be ahead of your local copy
git merge --no-ff refactor/modernize-jev -m "Merge refactor/modernize-jev into main"
uv run ruff check .                      # then the full §4 verification on the merge commit
git push origin main
```

### If `main` moved again before you merge

Bring it into the branch first, so the merge commit you test is the one that
lands:

```bash
git checkout refactor/modernize-jev
git merge origin/main
uv run ruff check . && uv run pytest tests -q     # re-verify ON the merge result
git push origin refactor/modernize-jev
```

That is the loop the PR runs for you, but doing it locally means a semantic
conflict — two changes that are textually clean and behaviourally incompatible —
is caught by you rather than by a red check after the merge.

### How you know the merge worked

```bash
git log --oneline -3                                   # the merge commit is HEAD
git diff --stat refactor/modernize-jev                 # empty: branch == main content
git status -sb                                         # in sync with origin/main
```

---

## 4. Verify — step by step

### 4.1 Lint

```bash
uv run ruff check .
```

**Expect:** `All checks passed!`
Ruff is a gate here, not a suggestion. It also enforces the one non-obvious rule
in this repo: **every string in `src/iris_ai/cli/*.py` must be cp1252-encodable**,
because a Windows console cannot render anything else and the failure is a
traceback, not a missing glyph.

### 4.2 The fast suite (no database)

```bash
uv run pytest tests -q \
  --ignore=tests/test_memory_pipeline.py \
  --ignore=tests/test_retrieval_gate.py
```

**Expect:** `883 passed` (~50 s).

The two ignored files are the *only* tests that need Postgres, and they fail
loudly rather than skipping when it is absent — deliberate, because a silently
skipping gate is how the memory pipeline stayed dark for a whole phase. If you
run them without a database you will get an explicit message telling you to start
one.

### 4.3 The full suite (needs Postgres)

```bash
docker compose up -d postgres
docker compose ps                       # wait for "healthy"
uv run pytest tests -q
```

**Expect:** `891 passed`.

**No `CREATE DATABASE` step is needed.** The suite's two scratch databases
(`iris_test` for the memory pipeline, `iris_eval` for the eval corpus) are created
on demand by `tests/db.py` when the fixture connects. If you want them gone, drop
them; the next run recreates them. (`postgres/init.sql` also creates `iris_eval`,
but only on a volume's *first* boot, which is why the fixtures no longer rely on
it.)

**Expect the same 891 whether or not those databases already exist** — if you get
a different number, something is order-dependent, and that is worth knowing.

### 4.4 The retrieval gate on its own

```bash
uv run pytest tests/test_retrieval_gate.py tests/test_retrieval_metrics.py -q
```

**Expect:** `13 passed`.

This is the job CI runs on its own. It is the model-free half of the eval story:
`recall@k` / `nDCG@k` over a labelled fixture through the real ranking code, with
no API key and no network. `test_retrieval_metrics.py` is pure arithmetic over
canned rankings, so the maths is still gated on a machine with no Postgres at all.

### 4.5 The artifact (the wheel)

A build that only exists in `pyproject.toml` is a claim, not a deliverable — so
build it and run it from an installed copy:

```bash
uv build                                                     # dist/*.whl + *.tar.gz
python -m venv /tmp/iris-wheel                               # Windows: .venv\Scripts\...
/tmp/iris-wheel/bin/pip install dist/*.whl
cd /tmp                                                      # NOT the repo: see below
/tmp/iris-wheel/bin/iris version
/tmp/iris-wheel/bin/iris --help > /dev/null && echo "help ok"
/tmp/iris-wheel/bin/iris skills list | grep -q web-page-to-notes && echo "builtin ok"
```

**Expect**, in order: `iris 0.2.0` (plus the interpreter and the installed package
path), `help ok`, `builtin ok`.

The `cd /tmp` matters: run from the repo root and `skills/` is on the path
anyway, so the check passes even when the *packaged* copy is missing. That exact
bug shipped once — the builtin skill was absent from the wheel while the README
advertised it.

### 4.6 The container

```bash
docker build -t iris:local .
docker run --rm --entrypoint id iris:local -u     # expect 10001, not 0
docker run --rm iris:local id                     # expect uid=10001(iris) gid=10001(iris)
```

Build first: `pyproject.toml` declares `license = { file = "LICENSE" }`, so the
image will not build if `LICENSE` is not in the build context, and the wheel
force-includes `skills/` and `assets/mermaid`, so those must be copied too. Both
are fixed in the Dockerfile; if a future edit drops one, this step is where it
shows up.

**The image needs a database to boot, on purpose.** The API keeps
`postgres="require"` (`engine.harness`), so without one it exits at startup
instead of serving a half-broken service:

```bash
docker run --rm iris:local                        # exits: "Application startup failed"
```

That is the API's contract, not a bug — the CLI is the component that degrades
(`postgres="auto"`). To run the whole stack, use compose, and note the two mounts
the comments in `docker-compose.yml` explain:

```bash
docker compose up -d
docker compose ps                                 # wait for healthchecks
curl -fsS http://127.0.0.1:8000/health
```

**Expect** a 200 with a body like this one (captured from this branch):

```json
{"status":"ok","service":"iris",
 "memory":{"total_chunks":0,"by_origin":{}},
 "jev":false,"telegram":false,
 "judgment":{"enabled":false,"model":"jev-latest","reason":"TYPESAFE_API_KEY is not set",
             "requests":0,"failures":0,"last_latency_ms":0,"last_error":""},
 "background":{"pending":0}}
```

`"status":"ok"` with `memory.total_chunks: 0` on a fresh volume is correct — an
empty index is not an unhealthy one. Once you have talked to her, that count
should rise; if it stays at 0 while chats succeed, that is a real fault (see
§6).

**On Linux, `WORKSPACE_DIR` is a bind mount owned by the host user.** The image
runs as uid 10001, so a host `./workspace` must be writable by it:

```bash
sudo chown -R 10001:10001 ./workspace
```

### 4.7 The API

```bash
TOKEN=$(grep -m1 '^IRIS_API_TOKEN=' .env | cut -d= -f2-)   # never echo this
curl -fsS -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/tools | head -c 200
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/tools     # expect 401
```

**Expect** an authorization failure without the token, and with it a JSON policy
snapshot on this branch:

```json
{"budget":20,
 "visible":["memory_search","deep_dive","verify_answer","remember","note","inspect_mind",
            "forget","file_create","file_write","file_read","file_list","web_search",
            "ingest_url","skill_list","skill_apply","schedule_task","find_tools",
            "skill_run","skill_write","skill_revise"],
 "deferred":["dream_now","get_chat_history","send_message","send_photo"], ...}
```

`visible` + `deferred` is the whole declared surface, and the deferred four are
the ones the prompt advertises as loadable. If `computer` appears, someone
enabled computer-use — worth noticing.

Other readouts worth a glance: `/costs` (spend and prompt-cache hit rate),
`/jev` (judgment health and *why* it is disabled), `/traces` (the last turns),
`/mind` (memory shape).

### 4.8 One real conversation — the step no test replaces

**This one writes to your real memory.** Do it when you are ready for that, and
know that `--session` decides which thread it joins.

```bash
uv run iris doctor                       # providers, workspace, degraded mode, key presence
uv run iris tools                        # the policy table + the deferred catalog
uv run iris skills list                  # every skill, its source, and whether it is usable
uv run iris chat --once "remember that I prefer green tea"
uv run iris chat --once "what tea do I prefer"      # same session: the round trip
```

**Expect:** the first turn acknowledges the note; the second answers from
memory. The second is the one that matters — it is the only check that proves
write → index → retrieve → answer end to end, and it is exactly what the fast
suite cannot cover.

If the round trip is silent, check in this order: `iris doctor` (is a key
configured and does it boot in `full` mode?), `/mind` (did anything get indexed?),
then `/traces` (did the turn call `remember` at all?). Rejections are recorded
with their reason.

The CLI needs a reachable provider (§6 — Ollama is not a fallback that works
without the daemon running).

---

## 5. CI — what each check proves, and how to run it locally

Five jobs (`.github/workflows/ci.yml`). The point of naming them separately is
that a failure names itself instead of hiding inside a 900-test run.

| Job | Proves | Run it locally |
|---|---|---|
| `lint` | `ruff check .` is clean | `uv run ruff check .` |
| `retrieval` | ranking regressions fail a PR, with no model in the loop | `uv run pytest tests/test_retrieval_gate.py tests/test_retrieval_metrics.py -q` |
| `package` | the wheel builds, installs, and its console entry works — including the *packaged* builtin skill | §4.5 |
| `test` | the full suite against a real pgvector Postgres | §4.3 |
| `build` | the production image builds and runs as a non-root user | §4.6 |

The workflow **no longer creates the scratch databases**: the fixtures do
(`tests/db.py`). Reaching into the pgvector service container with
`docker ps --filter publish=5433` was the fragile half of two jobs, and a service
container never runs `postgres/init.sql` anyway.

### 5.1 Reproduce the CI runner on your own machine

CI is Linux; this repo is developed on Windows, which is how two "passing" tests
turned out to be Windows-only. To run the suite the way the runner does — a
throwaway checkout on a real Linux container, talking to the same Postgres, with
your working tree mounted **read-only** so nothing of yours can be touched:

```bash
export MSYS_NO_PATHCONV=1                                   # Windows only
docker run --rm \
  --mount "type=bind,source=<ABSOLUTE-REPO-PATH>,target=/src,readonly" \
  --network iris_default \
  -e UV_PROJECT_ENVIRONMENT=/work/.venv -e UV_CACHE_DIR=/tmp/uvcache \
  -e WORKSPACE_DIR=/work/workspace -e SANDBOX_DIR=/work/workspace/sandbox \
  -e IRIS_TEST_POSTGRES_DSN=postgresql+psycopg://iris:iris_dev_password@postgres:5432/iris_test \
  -e IRIS_EVAL_POSTGRES_DSN=postgresql+psycopg://iris:iris_dev_password@postgres:5432/iris_eval \
  -e LLM_PROVIDER=ollama -e OLLAMA_BASE_URL=http://127.0.0.1:1 -e IRIS_API_TOKEN=ci-token \
  -w /work python:3.13-slim \
  bash -lc "apt-get update -qq >/dev/null && apt-get install -y -qq libpq5 git >/dev/null 2>&1 \
    && cp -r /src/. /work/ && pip install --quiet uv >/dev/null && uv sync --frozen >/dev/null 2>&1 \
    && uv run --frozen pytest tests -q"
```

**Expect:** `890 passed, 1 skipped` (the skip is platform-conditional and runs on
Windows, which is why local runs report 891).

Three things about that command are load-bearing: `libpq5` (psycopg needs it;
`ubuntu-latest` has it, a bare `slim` image does not), `cp -r /src/. /work/`
(zero stale `__pycache__`, a writable tree, and a read-only mount still protecting
your host), and `-e UV_PROJECT_ENVIRONMENT` (so the Linux venv never lands in your
checkout).

---

## 6. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `failed to connect to the docker API … dockerDesktopLinuxEngine` | Docker Desktop is installed but not running | Start Docker Desktop; wait for `docker info` to succeed |
| `docker exec … relation "…" does not exist` / asyncpg `InvalidCatalogNameError` | A scratch database is missing *and* could not be created | Start Postgres; confirm `CREATE DATABASE` privileges (the `iris` user is the superuser in the compose image) |
| `ImportError: no pq wrapper available` | No libpq, so psycopg cannot load | Linux/container: install `libpq5`. Windows: use the project's own venv via `uv run` |
| `no Postgres is listening at the test DSN` | Postgres is not up (the tests do not skip) | `docker compose up -d postgres`, wait for `healthy` |
| `Application startup failed. Exiting.` from the image | The API requires a database (`postgres="require"`) | Give it a reachable `POSTGRES_DSN`, or run the stack with `docker compose up -d` |
| `iris chat` says it hit a provider hiccup / rate limit | No provider reachable (e.g. Ollama not running, or a key is unset) | `uv run iris doctor` says which provider is in use and whether its key is present |
| `License file does not exist: LICENSE`, or `Forced include not found: /build/skills` | A Dockerfile `COPY` lost its build metadata / package data | Restore `COPY … LICENSE` and `COPY skills ./skills` + `COPY assets ./assets` |
| The PR's secret scan flags `tests/test_redaction.py` | The fixtures are credential-*shaped* on purpose | Regenerate them rather than writing literals; see `_filler` in that file and `.gitguardian.yaml` |
| A test passes locally and fails on the runner | Almost always a platform assumption (path separators, console width, terminal) | Reproduce with §5.1, then fix the assertion, not the test |

---

## 7. What this document does **not** verify

Stated plainly, because a runbook that implies more coverage than exists is worse
than a short one:

- **No judge-based evaluation.** Retrieval ranking is gated deterministically
  (§4.4); answer *quality* is not measured by anything in CI, and the eval lab
  (`scripts/eval_lab.py` → `reports/eval_lab.md`) is run by hand, on a fixture
  corpus, with hash embeddings. Its numbers test the retrieval *policy*, not the
  embedder or the model.
- **No load, soak or concurrency testing.** Single-process, single-owner.
- **No live provider check.** CI runs against fakes and an unreachable Ollama on
  purpose, so a paid provider is never called from a PR. §4.8 is where a real
  model is exercised, by you.
- **No kernel boundary.** Skill scripts run in a subprocess with a constructed
  environment, an argv (never a shell), a timeout and capped output — process
  isolation, not a VM. `docs/deployment.md` states the limit.
- **`docker build` is not verified on Windows CI**, only on Linux, because that
  is where the image runs.

---

## 8. After the merge

| Next | Where |
|---|---|
| Publish (your steps, not an agent's) | `docs/NEXT-SESSION.md` §5 — trusted publisher, tag `v0.2.0`, release workflow |
| What the vault recommends vs what Iris has | `docs/vault-review.md` (its backlog is empty) |
| Module map, invariants, where state lives | `docs/architecture.md` |
| Adding a tool / skill / channel / eval metric | `docs/extending.md` |
| Support matrix and how a release is cut | `docs/support.md` |
| The turn pipeline, traces and costs | `README.md` → Observability |

Verify first, merge second, publish third. In that order, and only the first one
is something an agent can do for you.
