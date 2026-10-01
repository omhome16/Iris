# The harness

Iris is a neutral agent harness. The package and the `iris` command keep that
name. There is no persona until you write one. The picture of a session and of
one message is [architecture.md](architecture.md).

## First run

```bash
uv sync
uv run iris init          # config, a blank profile, then a probe
uv run iris chat          # full-screen chat when the terminal is a TTY
```

`iris chat --once "hello"` and a pipe stay plain text. `iris init --yes` writes
the blank profile without the setup screen, which is what CI uses. `iris config`
opens the same screen later.

The profile lives in `workspace/config/iris.json`. Anything you want the model
to *be* goes in `workspace/PERSONA.md`. Leave that file empty and the system
prompt is only the harness contract.

## Swap a piece

`config/harness.toml`:

```toml
[components]
context = "default"          # or pkg.module:Class
capture = "default"          # or off, or pkg.module:Class
consolidator = "dreaming"    # or off, or pkg.module:Class
```

A class is constructed with the runtime.

- **Context** implements `assemble_turn(user_message, *, session_id) -> (text, skill_names)`.
  The built-in assembler reads `MEMORY.md` and `USER.md`. It does not search the
  index. The model recalls with the `memory_search` tool.
- **Capture** implements `maybe_capture(...)`.
- **Consolidator** implements `sleep()`.

Scaffold one:

```bash
uv run iris new component context
uv run iris new component memory
```

Examples you can copy: `examples/custom_context` and `examples/custom_memory`.

## Threads

A conversation is a thread in SQLite (`workspace/threads.db`) by default. Tests
use an in-memory store. Postgres is the `postgres` extra. A paused approval is
stored on the thread and resumed with the same decision the tool was waiting for.

## Extras

The core install is the chat, the SQLite memory, and the CLI.

| extra | what it adds |
|---|---|
| `postgres` | pgvector memory and shared threads |
| `api` | `iris api` (FastAPI) |
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
