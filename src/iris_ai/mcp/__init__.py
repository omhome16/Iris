"""MCP servers as a declared, trusted capability pool (Phase 3, first slice).

The roadmap's Phase 3 asks for "add any MCP server from config; per-server trust
and approval". This module is the *declaration* half of that, and the client half
lives next door in `iris_ai.mcp.client`. Both are deliberately small and
SDK-free/pure where they can be, because the interesting decisions here are
policy decisions, not protocol ones.

What is decided here:

- **Servers are declared, never hardcoded.** The file is the ecosystem's own
  shape (`.mcp.json`, `{"mcpServers": {...}}`), so an owner can bring a config
  they already have. A missing file means "no servers" — the same way a missing
  manifest means "defaults" — while a *malformed* one raises, naming the server
  and the key: a server the owner believes is connected but is not is worse than
  a startup error.
- **Trust belongs to the server, not the tool.** A tool's own `readOnlyHint` is
  the only thing a server can tell us about its effects, and it is self-reported,
  so it is worth exactly as much as the server is. `policy_for` turns those two
  facts into one decision: a read-only hint buys `allow` on any server; anything
  else is `deny` on an `untrusted` server, `ask` on a `review` one, and `ask` on
  an `owner` one. The deny is a *floor* — `approval = "never"` cannot open a
  write on an untrusted server, because "deny always wins" is the one rule
  `toolpolicy` already refuses to bend, and a second policy engine that bent it
  would be a second source of truth about safety.
- **Trust also decides whether the output is screened.** `review` and `untrusted`
  are *screened* levels: `iris_ai.mcp.provider` runs the same injection guard
  Iris already applies to web content over the reply, drops it outright when the
  guard blocks, and tags every reply as untrusted data. `owner` means the output
  is used as-is — which is the whole point of naming a server yours, and the
  reason the level is explicit rather than inferred.
- **Names are namespaced.** `server/tool`, always. Two servers can ship `search`
  without one silently shadowing the other, and a name in a trace says which
  server answered.

What is deliberately **not** here yet, stated rather than implied, because "there
is an MCP client" and "the model can call it" are different claims:

1. **Nothing is wired into the turn.** `open_server` connects and lists; the tool
   provider that registers these as `Tool`s, and the `toolpolicy` extension point
   that carries `policy_for`'s decision into the surface, are the next step. The
   static `TOOL_DECLARATIONS` table is asserted to cover core tools in *both*
   directions, so external tools need a declaration path rather than an entry.
2. **stdio cannot run under the CLI's event loop on Windows.** The MCP stdio
   transport spawns the server with asyncio subprocesses, and Windows' selector
   loop does not implement them — the same wall `skills/runner.py` hit, which is
   why skill scripts run `subprocess.run` on a worker thread instead. `iris chat`
   and `iris serve http` select that loop on Windows (psycopg needs it), so a stdio
   server declared there will refuse to start rather than half-work. The http
   transport is unaffected, and it is what the Telegram bridge already uses.

Secrets: `env` values are passed to the server process. `${VAR}` resolves from
the process environment at load time, which is the supported way to hand a token
to a server without writing it into a file that gets committed.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iris_ai.config import settings
from iris_ai.toolpolicy import Policy

#: Transports a declared server may name. `http` is streamable HTTP (the current
#: standard); `sse` is the older HTTP+SSE transport, kept because a lot of
#: published servers still speak it; `stdio` spawns the server as a child process.
#: There is no `ws`: the SDK ships no websocket client transport, and declaring
#: one that silently fell back to something else would be worse than refusing it.
TRANSPORTS: tuple[str, ...] = ("stdio", "http", "sse")

#: Three levels, and `untrusted` is the default on purpose: a server nobody
#: vouched for must not be able to write anything the first time it is connected.
#:
#: - `owner` — you wrote it or you run it for yourself. Output is used directly.
#:   (`trusted` is accepted as a synonym, because that is the word the design doc
#:   and most other clients use.)
#: - `review` — third-party, and you accept that it may write with your approval.
#:   Output is screened for instruction injection and tagged as data.
#: - `untrusted` — unknown. Writes are denied outright; output is screened and
#:   tagged, and must never be promoted into curated memory.
TRUST_LEVELS: tuple[str, ...] = ("owner", "trusted", "review", "untrusted")

#: The level actually used, since `trusted` is spelled `owner` internally.
_TRUST_ALIASES: dict[str, str] = {"trusted": "owner"}

#: Levels whose output is screened and structurally tagged as untrusted data.
SCREENED_TRUST: frozenset[str] = frozenset({"review", "untrusted"})

#: `auto` defers to the trust/read-only rule; `always` and `never` are the
#: owner's explicit exceptions for one server.
APPROVALS: tuple[str, ...] = ("auto", "always", "never")

_KEYS = frozenset(
    {"transport", "command", "args", "url", "cwd", "env", "trust", "approval", "enabled"}
)

_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class McpConfigError(ValueError):
    """A declared server that cannot be honoured. Never silently dropped."""


@dataclass(frozen=True, slots=True)
class McpServerSpec:
    """One declared server, as the owner wrote it.

    `transport` is inferred from the shape when it is not stated: a `url` means
    `http`, a `command` means `stdio`. Inference is generous because those are
    the ecosystem's two spellings; everything else is validated.
    """

    name: str
    transport: str = "stdio"
    command: str = ""
    args: tuple[str, ...] = ()
    url: str = ""
    cwd: str = ""
    env: Mapping[str, str] = field(default_factory=dict)
    #: `owner` | `review` | `untrusted` (see `TRUST_LEVELS`). Any spelling of
    #: `trusted` has already been normalised to `owner` by the parser.
    trust: str = "untrusted"
    approval: str = "auto"
    enabled: bool = True

    @property
    def screened(self) -> bool:
        """Whether this server's output must be screened before the model sees it.

        A property rather than a second field: two fields that can disagree
        about `trust` is exactly the drift `policy_for` exists to prevent.
        """
        return self.trust in SCREENED_TRUST


@dataclass(frozen=True, slots=True)
class McpToolInfo:
    """One tool as the *server* describes it.

    Deliberately not `iris_ai.agent.tools.Tool`: this is what the protocol said,
    before any name is namespaced or any policy applied, and the provider that
    adapts it is the thing that bridges the two.
    """

    name: str
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    read_only: bool = False
    destructive: bool = False


@dataclass(frozen=True, slots=True)
class McpPolicy:
    """The server's contribution to a tool's policy, with the reason for it."""

    policy: Policy
    reason: str


def _stored_secret(name: str) -> str | None:
    """One `${VAR}` looked up in the secret store, without leaking a value.

    Imported lazily so `iris_ai.mcp` stays importable (and cheap) for a caller
    that only wants to read a declaration file.
    """
    from iris_ai.secrets import lookup

    return lookup(name)[0]


def namespaced(server: str, tool: str) -> str:
    """`server/tool` — the only name the model ever sees for an external tool."""
    return f"{server}/{tool}"


def policy_for(tool: McpToolInfo, spec: McpServerSpec) -> McpPolicy:
    """What this server's tool may do before the owner's own overrides.

    Order matters, and it is the same order `toolpolicy` uses — an explicit
    setting is more specific than a class default, and a deny is never reopened:

    1. `approval = "always"` -> `ask`. The owner pinned this server, including
       its reads.
    2. read-only hint -> `allow`. It cannot write anything, by its own account.
    3. `trust = "untrusted"` -> `deny`. A self-reported "not read-only" from a
       server nobody vouched for is not something to approve the first time; the
       owner opts in by naming a level.
    4. `trust = "review"` -> `ask`. Third-party, and the owner accepted that it
       may write *with approval* — while its output is still screened, which is
       the half of "review" that makes it different from `owner`.
    5. `approval = "never"` -> `allow` (`owner` only, see 3 and 4).
    6. otherwise -> `ask`. A trusted server still gets a human in the loop before
       it changes something.
    """
    if spec.approval == "always":
        return McpPolicy(Policy.ASK, f"{spec.name}: approval pinned to always")
    if tool.read_only:
        return McpPolicy(Policy.ALLOW, f"{spec.name}: read-only per the server's own hint")
    if spec.trust == "untrusted":
        return McpPolicy(Policy.DENY, f"{spec.name}: untrusted server, tool is not read-only")
    if spec.trust == "review":
        return McpPolicy(Policy.ASK, f"{spec.name}: review-level server, tool is not read-only")
    if spec.approval == "never":
        return McpPolicy(Policy.ALLOW, f"{spec.name}: owner-level server, approval pinned to never")
    return McpPolicy(Policy.ASK, f"{spec.name}: owner-level server, tool is not read-only")


def _expand(
    value: str,
    environ: Mapping[str, str],
    *,
    where: str,
    strict: bool = True,
    fallback: Callable[[str], str | None] | None = None,
) -> str:
    """Resolve `${VAR}` from the environment, then from the secret store.

    The value is never echoed: this exists so a token does not have to live in a
    committable file, and printing it would undo that.

    `fallback` is the secret store (`iris_ai.secrets`), and it is passed only by a
    real load — a test that supplies its own `environ` gets exactly that
    environment, so a machine's stored secrets can never make a test pass.

    `strict=False` leaves an unset placeholder as written. That is for a server
    the owner has switched **off**: `enabled: false` is how a declaration is
    parked, and parking it must not require its secret to be present. Enabling it
    later fails then, loudly — which is the right time, because that is when the
    token is needed.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        found = environ.get(name, "") or (fallback(name) if fallback is not None else None)
        if found:
            return found
        if strict:
            raise McpConfigError(
                f"{where}: ${{{name}}} is not set in the environment or in the secret store "
                f"(set it there, or `iris secrets set {name}`)"
            )
        return match.group(0)

    return _VAR.sub(replace, value)


def _spec(
    name: str,
    raw: Any,
    *,
    where: str,
    environ: Mapping[str, str],
    fallback: Callable[[str], str | None] | None = None,
) -> McpServerSpec:
    if not isinstance(raw, dict):
        raise McpConfigError(f"{where}: expected an object, got {type(raw).__name__}")
    unknown = sorted(set(raw) - _KEYS)
    if unknown:
        raise McpConfigError(f"{where}: unknown key(s) {unknown}; known: {sorted(_KEYS)}")

    url = str(raw.get("url") or "")
    command = str(raw.get("command") or "")
    # Inferred from the shape when not stated, which is how the ecosystem writes
    # it: a `url` means a URL transport, a `command` means a child process.
    transport = str(raw.get("transport") or ("http" if url else "stdio"))
    trust = str(raw.get("trust") or "untrusted")
    approval = str(raw.get("approval") or "auto")

    if transport not in TRANSPORTS:
        raise McpConfigError(f"{where}: unknown transport {transport!r}; known: {list(TRANSPORTS)}")
    if trust not in TRUST_LEVELS:
        raise McpConfigError(f"{where}: unknown trust {trust!r}; known: {list(TRUST_LEVELS)}")
    trust = _TRUST_ALIASES.get(trust, trust)
    if approval not in APPROVALS:
        raise McpConfigError(f"{where}: unknown approval {approval!r}; known: {list(APPROVALS)}")
    if transport == "stdio" and not command:
        raise McpConfigError(f"{where}: a stdio server needs `command`")
    if transport in ("http", "sse") and not url:
        raise McpConfigError(f"{where}: an {transport} server needs `url`")

    # Read before expanding anything: a switched-off declaration is still checked
    # for shape (above) but is not required to resolve its secrets (below).
    enabled = bool(raw.get("enabled", True))
    env_raw = raw.get("env") or {}
    if not isinstance(env_raw, dict):
        raise McpConfigError(f"{where}: `env` must be an object")
    env = {
        str(key): _expand(str(value), environ, where=f"{where}: env {key}", strict=enabled, fallback=fallback)
        for key, value in env_raw.items()
    }
    return McpServerSpec(
        name=name,
        transport=transport,
        command=_expand(command, environ, where=f"{where}: command", strict=enabled, fallback=fallback),
        args=tuple(
            _expand(str(a), environ, where=f"{where}: args", strict=enabled, fallback=fallback)
            for a in raw.get("args") or ()
        ),
        url=url,
        cwd=str(raw.get("cwd") or ""),
        env=env,
        trust=trust,
        approval=approval,
        enabled=enabled,
    )


def load_servers(
    path: Path | None = None, *, environ: Mapping[str, str] | None = None
) -> list[McpServerSpec]:
    """Read the declared servers. A missing file is "no servers", not an error.

    The file is `settings.mcp_servers_file` (`.mcp.json` by default), in the
    ecosystem's shape:

        {"mcpServers": {"notes": {"command": "uvx", "args": ["mcp-server-notes"]},
                        "wiki": {"url": "http://127.0.0.1:8100/mcp", "trust": "owner"}}}
    """
    # The secret store is consulted only for a real load (`environ is None`): a
    # test passing its own environment gets exactly that environment, so a
    # developer's stored tokens can never make a test behave differently.
    explicit = environ is not None
    environ = os.environ if environ is None else environ
    fallback = None if explicit else _stored_secret
    source = Path(path) if path is not None else Path(settings.mcp_servers_file)
    if not source.is_file():
        return []
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:  # any unreadable file is one clear error
        raise McpConfigError(f"{source} is not valid JSON: {exc}") from exc

    entries = data.get("mcpServers", data) if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise McpConfigError(
            f"{source}: expected an `mcpServers` object mapping a name to a server"
        )
    return [
        _spec(str(name), raw, where=f"{source}: server {name!r}", environ=environ, fallback=fallback)
        for name, raw in entries.items()
    ]


def validate_entry(
    name: str,
    entry: Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> McpServerSpec:
    """Validate one declaration exactly the way a boot would.

    Public because `iris mcp add` writes files: a command that saves a
    declaration the loader would reject has made the owner's problem worse, so it
    checks its own output first — through this function, not a second validator
    that could drift from it.

    `environ` defaults to the process environment (so `${VAR}` resolves for the
    caller editing a real file); pass a mapping to make the check hermetic.
    """
    explicit = environ is not None
    source = os.environ if environ is None else environ
    return _spec(name, entry, where=f"server {name!r}", environ=source, fallback=None if explicit else _stored_secret)


def enabled_servers(path: Path | None = None) -> list[McpServerSpec]:
    """The declared servers that are switched on — what a boot would connect."""
    return [spec for spec in load_servers(path) if spec.enabled]
