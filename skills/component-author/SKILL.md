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

## Contract (iris/v1)

Set `api_version = "iris/v1"` in `component.toml` and list `permissions`
(`llm`, `memory.read`, `files.read`, `state`). `ctx.runtime` is not granted.

- `context`: `async def assemble(self, request) -> ContextResult`
- `memory`: every name in `MemoryBackend.REQUIRED`, including `nearest` and `list_chunks`
- `persona`: `def text(self) -> str`. No arguments besides `self`.
- `capture`: `async def extract(self, request) -> list[MemoryCandidate]`
- `consolidator`: `async def propose(self, request) -> ConsolidationPlan`

Import the types from `iris_ai.sdk`. The kernel writes memory. You return candidates or a plan.

The v0 `ComponentContext(runtime)` adapter was removed in 0.6. Write `assemble`, `extract`, and `propose`.

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
