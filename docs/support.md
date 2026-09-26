# Support, versions and releases

What Iris needs, what has been verified, and how a release is cut. The point of
this page is that "it works on my machine" is not a support claim.

## Supported environment

| Axis | Supported | Notes |
|---|---|---|
| **Python** | 3.12, 3.13 | `requires-python = ">=3.12"`. CI runs the test suite on the runner's default 3.x; `uv` resolves per the lock. |
| **OS** | Linux (CI, containers), Windows (development), macOS (expected) | The skill runner uses a worker thread around `subprocess.run` deliberately: the API selects the Windows `Selector` loop for psycopg, where `asyncio` subprocess support is unimplemented. Tests pass on Windows; CI is Linux. |
| **Postgres + pgvector** | **required for full memory** | The vector index uses `vector` columns and an HNSW index, so a plain `postgres` image cannot run it. Without it Iris boots in **degraded mode**: conversation works, vector recall does not, and `mode`/`degraded_reason` say so. |
| **Disk** | `workspace/` holds all memory as plain Markdown + JSONL | Back it up with `tar`; see `docs/deployment.md`. |

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
| Sample config parses; every key names a real setting | `tests/test_packaging.py` | A `.env.example` that documents a knob nobody reads is a lie. |

**Local verification** (what the phase logs record): `uv run ruff check .` and
`uv run pytest tests -q --ignore=tests/test_memory_pipeline.py`. Docker is out of
bounds in this environment, so `docker build` and the pgvector suite are CI-only
and labelled as such rather than claimed locally.

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
