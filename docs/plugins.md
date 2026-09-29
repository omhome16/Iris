# Plugins — install one, or write one

A plugin is an ordinary Python package. It advertises one or more capabilities
under an entry-point group, and Iris discovers it at boot. Nothing in `src/` is
edited, and nothing is imported until the config asks for it.

If you are adding a capability *to core*, the recipes are
[`docs/extending.md`](extending.md). This page is about staying outside core.

## What you can plug in

| Group | The plugin exposes | Selected by | See it with |
|---|---|---|---|
| `iris_ai.tools` | an object with `.name` and `.tools(runtime) -> list[Tool]` | installed and enabled by default | `iris plugins tools` |
| `iris_ai.channels` | a factory returning a `Channel` | `CHANNELS_ENABLED` / `CHANNELS_DISABLED` (or `[channels]` in the manifest) | `iris plugins channels` |
| `iris_ai.hooks` | a callable `attach(bus) -> None` | installed and attached at boot | `iris plugins hooks` |
| `iris_ai.models` | a factory returning a `ModelBackend` | `MODEL_BACKEND=<name>` | `iris plugins` shows the registry |
| `iris_ai.memory` | a factory returning a `MemoryBackend` | `MEMORY_BACKEND=<name>` | as above |
| `iris_ai.judges` | a factory returning a `Judge` | `JUDGE_BACKEND=<name>` | as above |
| *(no code)* | an MCP server in `.mcp.json` | the declaration file | `iris plugins mcp [--live]` |

The Protocols are short on purpose — read them before writing anything:

| Capability | Protocol | Required surface |
|---|---|---|
| tools | `iris_ai.toolregistry.ToolProvider` | `.name`, `.tools(runtime)` |
| channels | `iris_ai.channels.base.Channel` | `.name`, `.connected`, `connect`, `close`, `send_message`, `send_photo`, `get_chat_history` |
| models | `iris_ai.capabilities.models.ModelBackend` | `complete`, `complete_with_tools`, `stream_complete_with_tools`, `embed`, `embed_one` |
| memory | `iris_ai.capabilities.memory.MemoryBackend` | `connect`, `close`, `search`, `escalate`, `stats`, `upsert_chunks`, `delete_file_chunks`, `replace_file_chunks`, `forget_entry` |
| judges | `iris_ai.capabilities.judges.Judge` | `enabled`, `unavailable_reason`, `ask`, `status`, `close` |

`REQUIRED` in each capability module is the same list, exported so the conformance
tests assert what this page claims rather than a copy that can drift. Start by
reading `tests/test_capability_interfaces.py` — it checks every core
implementation against the interface it is registered as, and it is the fastest
way to see what a conforming implementation looks like.

## A worked plugin: one tool and one hook

```
iris-audit/
├── pyproject.toml
└── iris_audit/
    ├── __init__.py
    └── plugin.py
```

```toml
# pyproject.toml
[project]
name = "iris-audit"
version = "0.1.0"
dependencies = []

[project.entry-points."iris_ai.tools"]
audit = "iris_audit.plugin:AuditTools"

[project.entry-points."iris_ai.hooks"]
audit = "iris_audit.plugin:attach"
```

```python
# iris_audit/plugin.py
"""One tool, and one hook that logs every tool call to a file.

Nothing here imports from `iris_ai.agent.tools` beyond `Tool`, and nothing
reaches into the engine: a plugin is a consumer of the public seams, exactly like
a client.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from iris_ai.agent.tools import Tool
from iris_ai.config import settings

LOG = Path(settings.workspace_dir) / "config" / "audit.jsonl"


def _last_call(runtime) -> str:
    """Read back the last tool call this plugin logged (it keeps no state of its
    own — the file *is* the state, so it survives a restart)."""
    if not LOG.exists():
        return "no tool calls recorded yet"
    lines = LOG.read_text(encoding="utf-8").splitlines()
    if not lines:
        return "no tool calls recorded yet"
    return lines[-1]


class AuditTools:
    """A `ToolProvider`: one read-only tool."""

    name = "audit"

    def tools(self, runtime) -> list[Tool]:
        return [
            Tool(
                "audit_last_call",
                "Show the most recent tool call this agent made.",
                {"type": "object", "properties": {}},
                lambda: _last_call(runtime),
            )
        ]


def attach(bus) -> None:
    """A hook plugin: subscribe to the lifecycle bus.

    Priority 0 (the default) runs after built-in policy (`-100`) and telemetry
    (`-50`) — an add-on observes, it does not pre-empt.
    """

    def on_post_tool(tool: str = "", ok: bool = True, **_) -> None:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {"at": datetime.now(timezone.utc).isoformat(), "tool": tool, "ok": ok},
                    ensure_ascii=False,
                )
                + "\n"
            )

    bus.on("post_tool", on_post_tool, name="audit")
```

Install it and check that it landed **before** starting a chat:

```bash
pip install -e ./iris-audit
iris plugins tools     # lists `audit`, and where it came from
iris plugins hooks     # attach it to a throwaway bus and show its subscribers
iris chat              # `audit_last_call` is now on the surface
```

## The rules every plugin inherits

These are enforced in `iris_ai/registry.py` and the boot path, so a plugin cannot
opt out of them:

- **Nothing is silently dropped.** A duplicate name is a conflict naming both
  sources. Core owns the name it registers; a plugin that must take it over has
  to say `replace=True`, explicitly.
- **An unknown name is an error, not a no-op.** `MODEL_BACKEND=typo` fails with the
  list of registered backends rather than falling back to something else.
- **Discovery is cheap.** Registering an entry point does not call it, so a
  disabled channel is never imported, let alone constructed. Config decides what
  runs.
- **A broken plugin is skipped, not fatal.** A tool provider that raises while
  being *built* is logged; a hook that raises while attaching is logged and
  skipped; a hook that raises while *running* is logged and skipped too
  (`HookBus.emit`). None of them costs a reply.
- **Plugin tools are additive.** They are not validated against `TOOL_NAMES` —
  that closed set exists so a *skill manifest* cannot name a core tool that does
  not exist — but they are logged when added, so "a core tool nobody declared"
  stays distinguishable from "a plugin's tool".
- **External tools are declared, not free.** A tool that reaches outside the
  process is subject to `toolpolicy` (`ask` by default, `deny` for an untrusted
  source) and to an approval the owner answers. There is no plugin API that
  bypasses the policy engine, deliberately.

## The no-code path

Most "I want a new capability" answers are an MCP server that already exists:

```bash
cp config/mcp.json.example .mcp.json
iris mcp add wiki --url http://127.0.0.1:8100/mcp --trust owner
iris plugins mcp --live
```

Servers carry their own command, URL and trust; tools are namespaced
`server/tool`; `${VAR}` is resolved through the secret store so a token never has
to sit in a committable file. The trust model, transports and per-tool policies
are documented in [`docs/extending.md`](extending.md#add-a-capability-from-an-mcp-server)
and [`docs/redesign/02-integrations-mcp.md`](redesign/02-integrations-mcp.md).

## Testing a plugin

Test the *contract*, not your own convenience wrapper:

```python
async def test_it_satisfies_the_protocol():
    from iris_ai.capabilities.memory import REQUIRED, MemoryBackend

    backend = MyBackend(...)
    assert isinstance(backend, MemoryBackend)          # runtime_checkable
    for name in REQUIRED:
        assert callable(getattr(backend, name))
```

Then assert real behavior at the seam the harness uses:

- a tool provider: the tool appears in `tool_schemas(runtime)` and its call
  result reaches the model;
- a channel: `connect()` returning `False` leaves boot working (the contract says
  a down transport is skipped, not fatal);
- a hook: a raising handler does not propagate (`HookBus.emit` pins this);
- a backend: build it through the registry, not by importing your class — the
  registry is what the engine uses.

Run the project's own gates on the plugin repository too: it is a Python package,
and `pytest` + `ruff` are the price of admission.
