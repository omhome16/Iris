# Support, versions and releases

What Iris needs, what has been verified, and how a release is cut. The point of
this page is that "it works on my machine" is not a support claim.

## Supported environment

| Axis | Supported | Notes |
|---|---|---|
| **Python** | 3.12, 3.13 | `requires-python = ">=3.12"`. CI runs the test suite on the runner's default 3.x; `uv` resolves per the lock. |
| **OS** | Linux (CI, containers), Windows (development), macOS (expected) | The skill runner uses a worker thread around `subprocess.run` deliberately: the API selects the Windows `Selector` loop for psycopg, where `asyncio` subprocess support is unimplemented. Tests pass on Windows; CI is Linux. |
| **Memory store** | **SQLite by default — nothing to install.** Postgres + pgvector is the scale-up option | The default backend is one SQLite file (`SQLITE_PATH`); threads fall back to a second one. With pgvector (`MEMORY_BACKEND=pgvector` + `POSTGRES_DSN`) the vector index needs `vector` columns and an HNSW index, so a plain `postgres` image cannot run it. If the configured store is unreachable Iris boots in **degraded mode** (or refuses, for the API's `postgres="require"`), and `mode`/`degraded_reason` say which. |
| **Disk** | `workspace/` holds all memory as plain Markdown + JSONL | Back it up with `tar`; see `docs/deployment.md`. |
| **Docker** | optional | Needed for Postgres, for the full API + bridge stack, and for the `container` skill-sandbox level. The default path starts nothing. |

## Compatibility matrix — the optional surfaces

Each of these is additive: absent, its feature degrades with a reason rather than
failing the boot, and no core path imports it.

| Surface | Extra / requirement | When it is absent |
|---|---|---|
| Editor integration (ACP) | `pip install "iris-personal-ai[acp]"` | `iris-acp` exits non-zero naming the extra; `iris` is untouched |
| Telemetry (OTLP) | `pip install "iris-personal-ai[otel]"` | `OTEL_EXPORTER=none` is the default; asking for `otlp` without the extra is a **boot error** naming the extra, never a silent no-op |
| MCP servers, HTTP/streamable | the `mcp` SDK (a core dependency) | no declared servers means no external tools, and nothing is started |
| MCP servers, stdio | the `mcp` SDK **and** an event loop with subprocess support | refused **with the reason**: on Windows the CLI's selector loop does not implement asyncio subprocesses — run the server yourself and use `url` |
| MCP servers, `ws://` | not supported | refused rather than downgraded, because this SDK ships no websocket client transport |
| Secret store, OS keychain | `pip install keyring` | `SECRET_STORE=auto` chooses a 0600 file and says so; `iris secrets` reports which backend and where |
| Skill sandbox, container level | `EXEC_SANDBOX=container` + Docker | **fails closed**: the call is refused rather than run at the weaker `process` level |
| Computer use | `COMPUTER_ENABLED=true` + Playwright | the `computer` tool is not registered at all, and `availability()` reports a stable reason |
| `sqlite-vec` native vectors | *not used* | the SQLite backend always runs keyword (FTS5) recall and, when an embedding provider exists, **brute-force exact cosine over the rows**. Chosen over a native extension so the default path has no build step; `pgvector` is where approximate search at scale lives |

## Budget counters, and what is real

`workspace/config/budget.json` holds today's spend split by kind, because they
fail differently: `input`, `output`, `cached` and `embedding` each have a live
source (the model client reports them). `tool_schema` is **reserved**: tool
schemas are real spend but arrive inside `prompt_tokens`, and no provider reports
them separately, so nothing increments that bucket today. It stays in the schema
so a future source does not change the file's shape under a consumer.

`iris guards` (and `GET /guards` on a running engine) prints the same numbers the
ceiling is enforced against. `0` means **no ceiling** everywhere, not "a limit of
zero".

## Providers

| Layer | Required? | Behaviour when absent |
|---|---|---|
| Chat/embedding provider (Gemini / Groq / OpenRouter / Ollama) | one is required for model calls | Without one, deterministic paths still run; model-backed features degrade. `iris doctor` prints which key **names** are present — never values. |
| TypeSafe JEV (`TYPESAFE_API_KEY`) | optional | Recall reranking, skill selection and untrusted-content screening fall back to their deterministic paths. Verified live at model `jev-1.13.0`. |
| Telegram (`TELEGRAM_BOT_TOKEN`, `OWNER_CHAT_ID`) | optional | The bridge is simply not started; the `send_message`/`send_photo`/`get_chat_history` tools are not registered, and their schemas are deferred off the visible surface. |
| TypeSafe JEV on the reply path | optional | The recall rerank carries its own latency budget (`JEV_RERANK_TIMEOUT_SECONDS`, default 2.5 s) inside the client timeout: past it the deterministic shortlist is used and the turn keeps moving. A judgment layer must never become a latency dependency. |
| Playwright (`iris[computer]`) | optional | Computer-use stays `unavailable` with a stable reason. `COMPUTER_ENABLED=false` means the `computer` tool is not registered at all. |
| Tavily (`TAVILY_API_KEY`) | optional | `web_search` reports a clear failure; nothing else changes. |

## What CI verifies, and where

| Check | Where | Why there |
|---|---|---|
| `ruff check .` | CI `lint` job | Lint is a gate, not a suggestion. |
| `uv build` + install the wheel + run `iris version` / `iris --help` | CI `package` job | The wheel is the artifact. A build that only exists in `pyproject.toml` is a claim, not a deliverable. |
| `pytest tests -q` (full, including the DB-backed suite) | CI `test` job | The five `test_memory_pipeline.py` tests need a real pgvector service; locally they fail loudly rather than skipping, so CI is where they always run. |
| `recall@k` / `nDCG@k` over a labelled fixture, no model in the loop | CI `retrieval` job | The retrieval eval used to be a report generated by hand, and a hand-generated report is not a gate. Deterministic metrics mean a ranking regression fails the PR rather than waiting for a judge suite. |
| The production image builds and runs as a non-root user | CI `build` job | A Dockerfile is only correct if it builds: the wheel's metadata needs `LICENSE` in the context, and its forced package data needs `skills/` and `assets/mermaid`. Both were missing and this job is what caught it. |
| The five-minute path, **timed**, with no Postgres service in the job | CI `onboarding` job | Clone → `uv sync` → `iris init --offline` → a reply, and the job fails past 300 s. "No daemon" is half the claim, so the job that tests it deliberately has no daemon. |
| Dependency audit (known CVEs) + static analysis (`bandit`, medium and up) | CI `security` job | A pinned lockfile is not the same as a safe one: this job caught `httpx2` 2.10.0 (three advisories, reached through the MCP SDK). The two skipped bandit rules are named and justified in the workflow. Secret scanning runs as GitGuardian's app on the repository (`.gitguardian.yaml`). |
| Sample config parses; every key names a real setting | `tests/test_packaging.py` | A `.env.example` that documents a knob nobody reads is a lie. |
| The packaged neutral profile and the tracked `workspace/` copy are byte-identical | `tests/test_packaging.py` | Two copies of a default drift; the test is cheaper than discovering which one a clone read. |

The suite's two scratch databases (`iris_test`, `iris_eval`) are created on
demand by the fixtures that use them (`tests/db.py`), so nothing has to run
`CREATE DATABASE` first — locally or in CI.

**Local verification** (what the phase logs record): `uv run ruff check .` and
`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py
--ignore=tests/test_retrieval_gate.py`. Docker is out of
bounds in this environment, so `docker build` and the pgvector suites are CI-only
and labelled as such rather than claimed locally.

[`docs/MERGE-AND-VERIFY.md`](MERGE-AND-VERIFY.md) is the full runbook: merge,
verify each stage with its expected output, reproduce the CI runner locally, and
what each check does *not* cover.

## Version discipline

- **One source of truth.** The version lives in `src/iris_ai/__init__.py`
  (`__version__`), and hatchling reads it from there
  (`[tool.hatch.version] path = "src/iris_ai/__init__.py"`). `pyproject.toml` no
  longer hardcodes it, so the two cannot drift. A test asserts this.
- **The changelog is the release note.** `CHANGELOG.md` is written per phase,
  newest first, and every number in it names the method that produced it.
- **Cutting a release** (owner action — Iris does not tag or push itself):
  1. Decide the version and edit `src/iris_ai/__init__.py`.
  2. Move the `## Unreleased — …` section of `CHANGELOG.md` under the new version.
  3. `uv build`, then confirm CI's `package` job is green.
  4. `git tag vX.Y.Z` and push — an explicit, deliberate act, never automatic.
