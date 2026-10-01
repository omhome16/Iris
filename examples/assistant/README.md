# Reference app — a personal assistant

This is the assistant Iris was built to be, expressed as **configuration** rather
than code: a persona, a memory workspace, and a front door. It is the proof that
the harness is general — nothing in `src/` mentions a persona, Telegram, or a
life, and here is one assembled from a manifest and two Markdown files.

The harness it exercises is documented in [`DOCS.md`](../../DOCS.md).

## Run it

```bash
# 1. From the repository root, point the harness at this profile.
export HARNESS_CONFIG=examples/assistant/harness.toml

# 2. Create .env (if you have not), and set at least one provider key in it.
iris init

# 3. Talk to it. No daemon, no Postgres, no Docker.
iris chat
```

That is the whole setup. `iris init` writes `.env`, keeps this profile's
manifest (it is already where `HARNESS_CONFIG` points), and then proves the
setup with one no-op model call, an opened index and the real thread store.
The profile sets its own `workspace_dir`, `sqlite_path` and
`checkpointer_path`, so nothing you already have is touched or mixed.

Prefer the container? The compose stack reads the same manifest with
`HARNESS_CONFIG` exported, so `docker compose up -d` runs the personal assistant
while `iris chat` above runs the same agent without it.

Telegram needs two things in `.env` (`TELEGRAM_BOT_TOKEN`, and `OWNER_CHAT_ID`,
learned from the first `/start` if you leave it blank) and the bridge running:

```bash
uv run python scripts/run_bridge.py     # the MCP bridge the channel talks to
```

Until then the channel is simply not connected — `iris chat` and the HTTP API work
the same, and boot says which channels are missing rather than failing.

## What is in here

| Path | What it is |
|---|---|
| `harness.toml` | the profile: its own workspace, the Telegram channel, `agent_name` |
| `workspace/AGENTS.md` | the operating contract — memory discipline, proactivity, safety, voice |
| `workspace/README.md` | what each file in a workspace is for, for a human reading the directory |
| `workspace/` (runtime) | `MEMORY.md`, `USER.md`, `DREAMS.md`, `memory/`, `skills/`, `config/` — created as it runs, gitignored here |

## What it demonstrates

- **A profile is configuration.** Compare this directory with
  `src/iris_ai/templates/`: the harness's default is *neutral* (no name, no
  channel, no persona), and this is one overlay on top of it. The two are kept
  byte-identical where they overlap, by a test
  (`tests/test_packaging.py::test_the_default_profile_templates_are_the_tracked_workspace_files`),
  so neither can drift into saying something the other does not.
- **Memory is files.** Everything the assistant knows is in `workspace/` as
  Markdown. The index is derived: delete it and it is rebuilt.
- **Identity is set in `iris config`.** That screen writes `workspace/config/iris.json`,
  `USER.md`, and `PERSONA.md`. Nothing is hard-coded, and chat does not ask the
  questions itself.
- **The safety story is the same one.** Approvals, sandboxing, injection
  screening and budgets come from the harness, not from this profile. There is no
  special-cased "assistant mode" that could be less careful.

## Making it yours

Copy the directory, edit `workspace/AGENTS.md`, and point `WORKSPACE_DIR` at your
copy. Personas, not forks: if your profile needs behavior the harness cannot
express, that is a missing capability interface — see
[§10 of the manual](../../DOCS.md#10-extending-iris) — rather than a reason to patch
`src/`.
