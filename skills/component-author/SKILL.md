---
name: component-author
description: Add a new context, memory, persona, capture or consolidator component to Iris itself. Use when the owner asks Iris to implement a new technique, paper, or plugin and start using it.
license: Apache-2.0
metadata:
  version: "1.0"
  iris-triggers: "implement this in yourself, add a context component, new memory system, build this into iris"
allowed-tools: component_write component_check component_activate component_rollback
---

# Add a component to Iris

Iris does not edit its own package. A new part is a folder under
`components/.staging/<kind>/<name>/`, checked in a separate process, and moved
into place only after the owner approves `component_activate`.

## Contract

- `context`: `async def assemble_turn(self, user_message, *, session_id) -> tuple[str, list[str]]`
- `memory`: `async def search(self, query, **kwargs)` plus `connect` and `close`
- `persona`: `def text(self) -> str`. `text` takes no arguments besides `self`. Do not add `original`, `text`, or `message`. `component_check` and `iris doctor` both call `text()` that way; a signature that needs another argument fails both.
- `capture`: `async def maybe_capture(self, *, user_message, reply, known_context) -> str`
- `consolidator`: `async def sleep(self)`

The constructor is `__init__(self, ctx, **options)`. `ctx` has `llm`, `memory`,
`files`, and `options`. Do not import Iris internals beyond that.

## Procedure

1. Decide the kind and a short hyphenated name.
2. `component_write` the `component.toml` (`kind`, `name`, `entry`, `description`).
3. `component_write` the `component.py` class.
4. `component_check`. If it fails, read the error and write the file again. Stop after three failures and tell the owner what blocked you.
5. Show the owner the files and the check result. Call `component_activate` only then. It asks for approval.
6. Tell the owner to type `/reload`. If the new component errors, `component_rollback` returns to the previous one.

## Rules

- Never write outside `components/.staging`.
- Never read or write `.env`.
- Never claim the component is live before `/reload`.
