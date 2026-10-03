# The harness

Iris is a neutral agent harness. The package and the `iris` command keep that
name. There is no persona until you write one. The picture of a session and of
one message is [architecture.md](architecture.md).

## First run

```bash
uv tool install iris-personal-ai
iris init
iris
```

`iris` opens the full-screen chat when the terminal is a TTY. `iris chat --once`
and a pipe stay plain text. `iris init --yes` skips the wizard, which is what CI
uses. `iris config [section]` reopens one step: provider, key, model, embeddings,
parts, mcp, or profile.

The profile lives in `workspace/config/iris.json`. Anything you want the model
to *be* goes in `workspace/PERSONA.md`. Leave that file empty and the system
prompt is only the harness contract.

## Swap a piece

`config/harness.toml`:

```toml
[components]
context = "default"          # default | recall-first | minimal | pkg.module:Class
persona = "file"             # blank | file | assistant | coder | researcher | tutor
capture = "default"          # or off, or pkg.module:Class
consolidator = "dreaming"    # or off, or pkg.module:Class

[agents]
max_parallel = 3
max_calls_per_turn = 2
budget_usd = 0
```

`iris components` lists the options. `iris components use <kind> <option>`
switches one. `iris new context|memory|persona|role|channel <name>` writes a
skeleton and points the manifest at it. Every picker also has a Create new entry.

A class is constructed with the runtime.

- **Context** implements `assemble_turn(user_message, *, session_id) -> (text, skill_names)`.
  `default` reads `MEMORY.md` and `USER.md` and does not search the index.
  `recall-first` searches memory every turn and puts the hits in front.
  `minimal` loads no memory.
- **Memory** is `memory_backend`: `sqlite` (default), `markdown` (files only, no
  index), or `pgvector`.
- **Persona** is a `PersonaSource`. Presets live in `src/iris_ai/templates/personas/`.
  `file` reads `workspace/PERSONA.md`. `blank` adds nothing.
- **Roles** are `[roles.<name>]` with `prompt`, `tools`, `model_tier`, and
  `max_steps`. Researcher and critic are the defaults. The model fans out with
  the `run_parallel` tool. In chat, `/team <question>` asks every role and
  `/parallel role: task | role: task` asks the ones you name.
- **Capture** implements `maybe_capture(...)`.
- **Consolidator** implements `sleep()`.

Scaffold one:

```bash
uv run iris new component context
uv run iris new component memory
```

Examples you can copy: `examples/custom_context` and `examples/custom_memory`.

## MCP

One file, `.mcp.json`. `iris mcp add filesystem` (or fetch, github, git,
brave-search, playwright, sqlite) writes a preset. `iris mcp add custom
--transport stdio|http|sse` writes one you name. `iris mcp enable` and
`iris mcp disable` toggle a server. In chat, `/mcp` lists presets and what is
declared. If the mcp extra is missing, the connect error includes
`uv sync --extra mcp`.

## Threads

A conversation is a thread in SQLite (`workspace/threads.db`) by default. Tests
use an in-memory store. Postgres is the `postgres` extra. A paused approval is
stored on the thread and resumed with the same decision the tool was waiting for.

## Extras

The core install is the chat, the SQLite memory, and the CLI.

| extra | what it adds |
|---|---|
| `postgres` | pgvector memory and shared threads |
| `api` | `iris serve http` (FastAPI) |
| `mcp` | MCP servers and the Telegram channel |
| `schedule` | nightly consolidation and reminders |
| `judge` | typed probability judgments |
| `acp` | the editor adapter |
| `otel` | OTLP traces |
| `all` | every extra above |

```bash
uv sync --extra all
```

## Settings

Settings stay one flat object (`iris_ai.config.settings`). Precedence is
defaults, then `.env`, then `harness.toml`, then a real environment variable,
then a CLI flag. The everyday keys are in `.env.example`. Rare ones are the
fields on `Settings` in `src/iris_ai/config.py`.
