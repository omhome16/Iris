# 02 — Integrations: MCP, channels, and trust

Iris's capability pool is MCP. This doc designs the MCP client, the server
registry, the transports, remote auth, per-server trust, and how channels fit.
It is grounded in the 2026 state of the ecosystem (see §7).

## 1. What the ecosystem settled on (researched)

- **Transports.** Claude Code supports `http` (alias `streamable-http`), `sse`
  (deprecated but still found), `stdio`, and `ws`. Codex uses `mcp_servers` in
  `config.toml` with stdio and HTTP, plus per-server approval modes. Streamable
  HTTP is the recommended remote transport; SSE is legacy.
- **Config compatibility.** `.mcp.json` / `mcpServers` blocks are the de-facto
  exchange format. An entry with a `url` but no `type` is a config error (read as
  stdio). Supporting this format means server docs work unchanged.
- **Remote auth.** MCP authorization is OAuth 2.1 + PKCE with RFC 9728 resource
  metadata; the Python MCP SDK (v1.23+) ships Resource Server support.
- **Discovery.** There is now an official MCP Registry plus vendor directories
  (Anthropic, Google Agent Registry, MLflow). A registry is metadata; the client
  still connects directly.
- **Server-push.** An MCP server can act as a *channel* that pushes inbound
  events into a session (Claude Code's Telegram/Discord/webhook pattern) — which
  is exactly how Iris already treats the Telegram bridge.

## 2. The MCP client

One client, N specs. A `McpClient` wraps a session and exposes `list_tools()`,
`call_tool(name, args)`, and (for channel servers) an inbound stream. It is
transport-agnostic behind a `Transport` interface:

| Transport | Use | Notes |
|---|---|---|
| `stdio` | local servers (`npx`, `uvx`, a script) | process lifecycle owned by the harness; env allowlist |
| `http` / `streamable-http` | remote servers | the default remote transport; OAuth-capable |
| `sse` | legacy remote | supported for compatibility, warned as deprecated |
| `ws` | servers that push events | header-only auth |

## 3. The server registry

A server is a `McpServerSpec`, declared in the manifest (and/or `.mcp.json`):

```toml
[mcp.servers.weather]
transport = "http"
url = "https://mcp.example.com/mcp"
trust = "review"            # trusted | review | untrusted
approval = "ask"            # allow | ask | deny  (per-server default)
always_load = false         # keep its tool schemas on the visible surface
timeout_s = 20
```

Design rules:

- **No core edit to add a server.** Config or `.mcp.json` only.
- **Per-server trust** decides the default approval for that server's tools and
  whether its output is screened for prompt injection (`05-safety.md`).
- **Namespacing.** Tools are exposed as `server/tool` (Claude Code/Cursor
  convention), so two servers cannot collide and a policy can name a whole server.
- **Tool surface budget.** A server with 80 tools does not flood the prompt:
  its tools are deferrable and surfaced on demand, unless `always_load`.
- **Health and reconnect.** A server that is down at boot is retried in the
  background (the channel retry pattern, generalized); a server that drops
  mid-session is marked unavailable for the turn rather than crashing it.
- **Secrets stay in env.** `headers`/`env` values may reference `${VAR}`; the
  manifest never holds a literal secret.

## 4. Channels are MCP servers that push

A `Channel` (`channels/base.py`, shipped) is the outbound half. The redesign
unifies it with MCP: a *push server* is an MCP server with server→client
messages, so Telegram, Discord and webhooks become one kind of integration rather
than a bespoke channel each. The `Channel` protocol stays the interface; an
`McpChannel` is one implementation of it and a native transport (e.g. a future
libpurple-style adapter) is another.

## 5. Remote auth (OAuth 2.1)

Remote HTTP servers may require OAuth 2.1. The client:

1. discovers resource metadata (RFC 9728) at the server's well-known endpoint;
2. performs dynamic client registration if offered, else uses a configured client;
3. runs the authorization-code + PKCE flow, with the callback on loopback;
4. stores tokens in the OS keychain (or an encrypted local store), never in the
   manifest or the journal;
5. refreshes transparently and surfaces "reconnect this server" in the CLI.

A static `Authorization: Bearer` header remains supported for simple servers.

## 6. Trust model

| Trust | Inbound content | Tool default | Use |
|---|---|---|---|
| `trusted` | used directly | `allow` for read, `ask` for write | first-party/first-person servers |
| `review` (default) | screened for injection; tagged untrusted | `ask` for write/network | most third-party servers |
| `untrusted` | screened + never promoted to memory | `deny` unless explicitly allowed | unknown servers, experiments |

This is the existing Iris provenance model (owner/agent/untrusted/system) applied
to *servers* as well as files, so "this text came from a server that can be
manipulated" is structural rather than a prompt warning.

## 7. Discovery (the registry) — later, not v1

The official MCP Registry and vendor directories are useful for a *catalog
browse* (`iris mcp search <name>` → writes a spec). It is deferred: discovery
without a trust story is how a user connects a hostile server. v1 supports manual
specs + `.mcp.json` import; v2 adds `iris mcp add` from a registry with a review
step.

## 8. What this replaces

| Today | Redesign |
|---|---|
| one hardcoded Telegram MCP URL | `McpServerSpec` registry, stdio/http/sse/ws |
| `Channel` protocol (shipped) | unchanged; MCP push servers implement it |
| no remote auth | OAuth 2.1 + token store |
| no server trust | `trust` + `approval` per server, namespaced tools |
| no registry browse | `iris mcp` verbs + optional registry search |
