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
| `DASHBOARD_USER` / `DASHBOARD_PASSWORD` | the dashboard proxies write routes (chat, `/sleep`, `/forget`) |
| `OWNER_CHAT_ID` | pins bridge ownership *and* enables the morning brief. Until it is set, the first `/start` claims the instance |
| `TYPESAFE_API_KEY` | optional; enables the JEV layer, falls back cleanly without it |

`IRIS_TIMEZONE` drives the sleep hour, the morning brief, and every daily-note
timestamp — set it to your real timezone or the scheduler fires at odd hours.

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
- `DASHBOARD_USER`/`DASHBOARD_PASSWORD` published on the landing page, and
  `IRIS_API_TOKEN` set;
- no `TELEGRAM_BOT_TOKEN`, so the demo has no channel into anything real.
