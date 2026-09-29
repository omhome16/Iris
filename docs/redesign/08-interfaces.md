# 08 — Interfaces: CLI, API, ACP, MCP-server mode

The kernel is one thing reached through several faces. Today: a CLI and an HTTP
API, plus the Telegram bridge as a client. The redesign adds the two protocols
that make a harness adoptable in 2026.

## 1. The library is the product

`import iris_ai; async with iris_ai.harness() as brain:` stays. Every interface is
a client of that. No interface gets behavior the library does not expose.

## 2. CLI (`iris`)

Kept and extended: `init` (a real first-run wizard), `chat`, `doctor`, `version`,
`skills`, `agents`, `cron`, `tools`, `guards`, `plugins`, plus new
`mcp` (add/list/remove/test servers), `policy`, `budgets`, `costs`, `migrate`.
Read-only inspection commands must work with no engine running, as `iris tools`
and `iris plugins` already do.

## 3. HTTP API

Kept: `POST /chat`, `/chat/stream` (SSE), `/chat/resume`, `/voice`, the memory and
admin routes, bearer auth. The SSE contract is shared with every client through
one `BrainClient` definition (already the case) — a client must not be able to
disagree with the server about the shape.

## 4. ACP — the IDE adapter (new)

The **Agent Client Protocol** is the "LSP for agents": a JSON-RPC standard that
lets editors (Zed, JetBrains, others) drive any conforming agent. Adding an ACP
adapter means Iris appears in a user's editor with no editor-specific code.

Scope: a thin adapter that maps ACP session/prompt/tool-permission messages onto
the kernel's turn API and approval events. It is a *client of the kernel*, like
the CLI — not a second brain. This is the single highest-leverage interface for
adoption and is why ACP is preferred over inventing an editor integration.

**Rejected:** a bespoke editor plugin per editor. ACP is the standard and already
has multi-vendor adoption.

## 5. MCP-server mode — let Iris be a tool (later)

The flip side of `02`: expose Iris's memory/tools *as an MCP server* so other
agents can use her. This makes Iris composable into someone else's harness. It is
deferred to v2 because it turns Iris into a capability provider with its own auth
and trust surface.

## 6. Clients & channels recap

- **Clients** (things that talk to Iris): CLI, HTTP clients, ACP adapters, the
  Telegram bridge. Idempotency is the client's job; `UpdateLedger` is the pattern.
- **Channels** (transports Iris reaches): `Channel` protocol / MCP push servers
  (`02`). Outbound, gated by policy.

## 7. Configuration & first run

`iris init` writes `iris.toml` by asking: which model provider (and key, stored in
`.env`), local SQLite or Postgres, which channels, which example profile. It
finishes by proving the setup with a single no-op model call, so "it is configured"
means "it works", not "files exist".

## 8. What this replaces / keeps

| Today | Redesign |
|---|---|
| CLI + HTTP API | kept, plus `mcp`/`policy`/`costs`/`migrate` verbs and `init` |
| Telegram bridge client | kept; channel registry generalized (`02`) |
| no IDE integration | ACP adapter |
| library `harness()` | kept as the one entry point |
