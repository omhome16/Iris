# ACP — Iris in an editor

The [Agent Client Protocol](https://agentclientprotocol.com) is how an editor
(Zed, JetBrains, Neovim clients, …) talks to an agent it spawned. `iris-acp` is
the process an editor runs; it speaks ACP on stdin/stdout and forwards every
prompt into the *same* harness the CLI and the HTTP API use.

```bash
pip install "iris-personal-ai[acp]"
iris-acp          # the editor spawns this; you normally do not run it by hand
```

There is no second brain in here. An ACP session is a client of `iris_ai.harness()`:
memory, judgments, approvals, budgets and traces behave identically whether the
owner is in a terminal, an HTTP client, or an editor. That is deliberate — an
adapter with its own copy of the turn would be a second behavior to debug, and
the first thing to drift.

## Configuration

| Setting | Effect here |
|---|---|
| `WORKSPACE_DIR` | the memory workspace every session uses; the client's `cwd` does **not** widen it (below) |
| `MODEL_BACKEND` / `MEMORY_BACKEND` / `JUDGE_BACKEND` | resolved exactly as for `iris chat` |
| `CHECKPOINTER_BACKEND` (`auto`) | `postgres` → `sqlite` → in-memory; `iris-acp` reports which tier it got |
| `OTEL_EXPORTER` | if `otlp`, ACP turns emit the same spans as every other interface |
| `HARNESS_CONFIG` | `config/harness.toml` is applied before the first turn |

An editor config for Zed looks like this:

```json
{
  "agent_servers": {
    "Iris": { "command": "iris-acp", "args": [] }
  }
}
```

Because the process inherits the environment, `.env` in the project root is
loaded the same way `iris chat` loads it.

## The four decisions the adapter makes

Read these before changing anything in `src/iris_ai/interfaces/acp/agent.py`.
Each is a place where the protocol and the harness disagree about something, and
the answer is written down rather than left to whichever code path ran first.

### 1. One harness, many sessions

An ACP `sessionId` maps to a LangGraph thread as `acp:<session_id>`, so
`session/load` continues the same conversation after an editor restart without a
second registry, and an ACP thread can never collide with the CLI's `cli` thread
or the API's `default`.

`session/close` forgets the *handle*, not the memory. A closed tab is not a
reason to lose what was said.

### 2. The editor's `cwd` does not widen the filesystem

`session/new` carries a `cwd` (and optionally `additionalDirectories`). The
session **records** it, reports it back through `session/list`, and logs it — but
file tools keep running against the harness workspace and its sandbox. A client
asking for a directory is not the same as an owner granting it, and a protocol
field is not a policy decision.

Widening scope per session would be a real feature; it needs an owner-visible
grant, not an implicit one. Until that exists, the answer is "recorded, not
granted" — and it is documented here so an editor that expects otherwise has
something to read.

Client-supplied `mcpServers` are treated the same way: they are logged and
**not adopted**, because nobody in this process vouched for them. The configured
servers (`.mcp.json`) are what runs.

### 3. An approval is a permission request

When the kernel interrupts for approval, the adapter turns it into
`session/request_permission`. The answer travels back through the same
`ApprovalGate` the CLI's `y/n` uses, so:

- a grant is **single-use** and bound to the tool call's id and argument digest;
- a dismissed dialog is a **refusal** (`cancelled`), never an exception and never
  a silent yes;
- the dialog shows the action, the digest and a sentence saying what approving
  allows — never the arguments themselves, for the same reason the traces hash
  them: an approval dialog must not become a place a secret is displayed.

One prompt may pause more than once (a turn can ask for two different actions).
`MAX_APPROVALS_PER_PROMPT` (8) bounds that; past it the turn stops and says so
rather than looping.

### 4. Unsupported prompt content is an error, not a silent drop

`initialize` advertises `PromptCapabilities()` — text only. `_text_of` therefore
flattens text blocks, renders a `resource_link` as a visible reference line, and
refuses anything else (`image`, `audio`, …) with `invalid_params`. Dropping a
block would answer a question the owner did not ask.

## What it maps

| ACP | Iris |
|---|---|
| `initialize` | protocol version 1, `loadSession: true`, text-only prompts, `agentInfo` from `iris version` |
| `session/new` | a new session id ⇒ a new `acp:<id>` thread |
| `session/load` | an existing id ⇒ the same thread, so memory continues |
| `session/list` | sessions this process knows, optionally filtered by `cwd` |
| `session/close` | forget the handle; cancel a running turn if there is one |
| `session/cancel` | cancels the running turn; the prompt answers `stopReason: cancelled` |
| `session/prompt` | one kernel turn, streamed back |
| `session/update` (agent message) | streamed text deltas, plus the final reply when nothing streamed |
| `session/update` (tool call) | `start_tool_call` on the model's request, `update_tool_call` with `completed` (or `failed`, read from the tool's own `{"ok": false}`) on the result |
| `session/request_permission` | a kernel approval interrupt |

`session/set_mode` is deliberately absent: Iris has no modes, so the router
answers `method not found` rather than inventing a no-op.

The **resumed** half of an interrupted turn returns the kernel's final reply; the
tool calls it makes while resuming are journalled but not streamed. Saying so
here is cheaper than a bug report asking why a resumed turn has no tool cards.

## Running it

`iris-acp` boots with `services=True` (scheduler and channels) and Postgres
`auto`: a machine with no database gets the SQLite checkpointer and degraded-but-
working memory instead of a process that refuses to start. A boot failure is
printed on stderr and exits non-zero, which is what an editor shows as "the agent
failed to start".

**stdout belongs to the protocol.** The streams are taken *before* anything else
and stdout is then redirected to stderr for the rest of the process, so a stray
`print` is a log line instead of a JSON-RPC parse error.

### Windows

The protocol's stdio transport needs `connect_write_pipe`, which
`WindowsSelectorEventLoopPolicy` does not implement — so unlike `iris chat`,
`iris-acp` leaves the event loop on the platform default (Proactor). psycopg
wants the selector loop, and the two cannot both win, so **on Windows an ACP
session uses the SQLite checkpointer even when Postgres is configured**. Threads
still survive a restart; they just live in a file. The same wall is documented in
`docs/support.md` for the skill runner.

## Where the tests are

| What | Where |
|---|---|
| The mapping decisions (recording stand-in for the client connection) | `tests/test_acp_adapter.py` |
| The wire itself — real `ClientSideConnection`, in-memory transport, so the methods, parameter names and models are the SDK's | `tests/test_acp_adapter.py::test_a_prompt_survives_a_real_jsonrpc_round_trip` |
| Approvals asked, approved, refused | `::test_an_approval_becomes_a_permission_request`, `::test_a_refused_approval_leaves_the_world_alone` |
| Cancellation answering `stopReason: cancelled` | `::test_cancel_stops_the_running_turn` |
| Text-only prompts refused with the reason | `::test_a_prompt_we_cannot_honor_is_an_error_not_a_silent_drop` |

A streamed approval is also the CLI's path (`iris_ai/cli/chat.py` consumes the
same `{"kind": "approval"}` custom event), which is why
`tests/test_chat_cli.py` and `tests/test_acp_adapter.py` fail together when the
interrupt is read wrongly. It is read by `chat._interrupt_value` in both paths.

The adapter tests skip, rather than fail, when the optional `acp` extra is
absent — the extra is required for the tests and optional for the harness.
