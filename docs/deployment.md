# Deploying Iris

Iris is not a serverless workload. She needs:

- an **always-on process** — Telegram long-polling plus APScheduler hold state
  that a scale-to-zero host would tear down between turns, and a 120 s turn
  budget does not survive a cold start;
- **Postgres with the `vector` extension** (the index uses `vector` columns and
  an HNSW index — a vanilla Postgres image will not start the app);
- a **persistent volume for `workspace/`**. This is not a cache. It *is* the
  product: `MEMORY.md`, `USER.md`, daily notes, dreams and skills are the source
  of truth, and the vector index is derived from them and rebuildable.

That rules out serverless-only platforms (Vercel/Netlify functions, Lambda
without a long-lived container). The realistic options:

| Host | Fits because | Trade-off |
|---|---|---|
| **Fly.io** | Machines stay on, Fly Postgres offers pgvector, volumes attach directly to the app, cheap for one small machine | Postgres is a separate app to manage; the free allowance has shrunk |
| **Railway / Render** | Dockerfile deploys straight from the repo, managed pgvector Postgres, persistent disks, HTTPS and a domain with no extra work | Persistent disk is a paid add-on; sleeping plans break the scheduler |
| **A small VPS** (Hetzner/DigitalOcean) + `docker compose` | Full control, the compose file in this repo *is* the deployment, cheapest per GB, easy volume ownership | You own patching, TLS (Caddy/nginx), and backups |

Whichever you pick, the same rules apply:

1. **Run the container as uid 10001** (it already does) and give it a workspace
   it can write:
   ```bash
   sudo chown -R 10001:10001 ./workspace
   ```
   A bind mount keeps the host's ownership, so skipping this is the single most
   common "Iris cannot remember anything" failure — writes fail silently into
   the log while the API stays up.
2. **`/health` is the readiness probe** (unauthenticated on purpose). Wire it to
   the platform's health check, or use the image's `HEALTHCHECK`.
3. **Never bake `.env` into an image.** `.dockerignore` excludes it; set env
   vars in the platform's secret manager instead.

## Environment

Copy [`.env.example`](../.env.example) and set at minimum:

| Variable | Why |
|---|---|
| `LLM_PROVIDER` + one provider key | `gemini` is the only provider that also serves embeddings; with no Gemini key, embeddings fall back to a local Ollama, which must then also be reachable |
| `POSTGRES_DSN` | must point at the pgvector database |
| `IRIS_API_TOKEN` | the API is the only thing standing between the internet and your memory; boot logs a warning when it is unset |
| `OWNER_CHAT_ID` | pins bridge ownership *and* enables the morning brief. Until it is set, the first `/start` claims the instance |
| `TYPESAFE_API_KEY` | optional; enables the JEV layer, falls back cleanly without it |

`IRIS_TIMEZONE` drives the sleep hour, the morning brief, and every daily-note
timestamp — set it to your real timezone or the scheduler fires at odd hours.

## Harness guards and budgets

Every tool call passes a deterministic, pre-dispatch chain —
`budget → circuit → spiral/dedup → record` — before the tool runs. Nothing in it
calls a model, so a guard cannot itself run away. The defaults are tuned for one
owner and 1–3 calls per turn:

| Variable | Meaning |
|---|---|
| `TOOL_GUARD_ENABLED` | `false` turns the whole chain into a no-op |
| `TOOL_MAX_CALLS_PER_TURN` | Growth ceiling; past this the turn stops whatever it is calling |
| `TOOL_SPIRAL_MIN_REPEATS` / `TOOL_SPIRAL_JACCARD` | The same tool with near-identical arguments this many times, and the argument similarity that counts as "the same call" |
| `TOOL_FAILURE_THRESHOLD` | Consecutive failures of the same tool that open its circuit for the rest of the run |
| `TOOL_FAILING_TOOLS_PER_TURN` | Distinct failing tools that escalate the whole turn |
| `BUDGET_MAX_TOKENS_PER_TURN` / `BUDGET_MAX_TOKENS_PER_DAY` | Token ceilings. **`0` means no ceiling.** The day ceiling is what stops a runaway that spends a little every turn |
| `APPROVAL_BIND_DIGEST` / `APPROVAL_GUARD_REPLAY` | Bind a resume to the digest of the action it showed, and let one `tool_call_id` grant once per thread |

The day counters persist to `workspace/config/budget.json` so the ceiling
survives a restart rather than resetting on redeploy; spending is split by kind
(input / output / cached / embedding / tool-schema), because they fail
differently. Every refusal lands in the turn trace as a `tool_guard` event with
its reason.

## Skill scripts

A skill that ships code (a `scripts/` directory) runs through one gated path:
`skill_run` resolves the file inside that skill's own directory, a deterministic
AST pre-screen flags network/credential/exec patterns, a JEV judgment gate
blocks anything it reads as unsafe, and the owner approves a run that carries
the findings and the exact arguments. The subprocess gets a **constructed
environment** — no `.env`, no provider keys — a timeout, and capped output.

**Residual risk, stated plainly:** this is *process* isolation, not *kernel*
isolation. Approving a script runs it as the same OS user as Iris, with that
user's filesystem access. An approved script can still read anything that user
can read, and there is no container boundary between Iris and the script. The
gate makes running unknown code a deliberate, informed act; it does not make it
safe. If you host skills you did not write, put the container itself behind the
boundary — a per-deploy sandbox or a separate machine — rather than relying on
the approval prompt.

## Computer-use

Screen control is **off by default** (`COMPUTER_ENABLED=false`), and off means
the `computer` tool is not registered at all — not present-but-refusing. When it
is on, the permission model is the boundary, not the driver:

| Variable | Meaning |
|---|---|
| `COMPUTER_PROVIDER` | `null` (default, no driver) or `playwright`. Any other value fails closed |
| `COMPUTER_ALLOWED_HOSTS` | Comma-separated host suffixes `navigate` may reach. **Empty means nothing may be navigated** |
| `COMPUTER_ALLOWED_APPS` | Comma-separated window/page title suffixes `click`/`type` may act in. Empty means nothing |
| `COMPUTER_MAX_ACTIONS` | Actions one approval buys |
| `COMPUTER_CONFIRM_DESTRUCTIVE` | Confirmation for `click`/`type`; a keystroke into a credential-looking field confirms regardless |
| `COMPUTER_ALLOW_OWNER_SCRIPTS_ONLY` | The kernel-boundary statement (default true) |

Every attempted action is appended to `workspace/config/actions.jsonl`: what,
where, allowed-or-not, and **never what was typed** (a length and a digest
instead). Read it with `iris tools actions` or `GET /actions`.

**Residual risk, stated plainly:** the driver runs in the Iris process with
access to the same session a browser would have. The allowlists bound *where* an
action may go, and the grant bounds *how many*, but neither is a hypervisor. For
untrusted or third-party automation, the prerequisite is a Wasm/microVM tier —
a container is not a containment boundary for code that can reach the screen.
Until that exists, keep `computer` to owner-authored, allowlisted flows.

## Backups

Two things to back up, in this order:

1. **`workspace/`** — the memory itself. It is plain Markdown and JSONL, so a
   nightly `tar` of the directory plus a copy off-host is a complete backup:
   ```bash
   tar czf iris-workspace-$(date +%F).tar.gz workspace/
   ```
   Restoring is just unpacking it; the index rebuilds itself on next boot.
2. **Postgres** — `pg_dump` on the same schedule. The database is *derived*, so
   losing it costs a reindex, not data — but the checkpointer tables hold
   in-flight conversations and pending human-in-the-loop approvals.

A restore that only restores the database is not a restore.

## Rollback

The app is a single image, so a rollback is a re-deploy of the previous tag:

```bash
docker compose pull iris-core && docker compose up -d iris-core
```

Schema changes are additive (`postgres/init.sql` runs only on an empty volume),
so an older image can run against a newer database. If a migration ever breaks
that, the previous tag is still the answer — the workspace is untouched by
deploys.

## Demo mode

To give visitors something to try without exposing real memory, deploy a second
instance with:

- its own empty `workspace/` (seed it by running `scripts/fresh_start.py` then
  holding a scripted onboarding conversation);
- its own database;
- `IRIS_API_TOKEN` set;
- no `TELEGRAM_BOT_TOKEN`, so the demo has no channel into anything real.
