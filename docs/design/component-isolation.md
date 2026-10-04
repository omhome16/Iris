# Component isolation

This is a design. It is not built. Components run in-process. The capability
context narrows the API a well-behaved component uses. It does not stop a
component from importing the runtime.

Build the boundary below when Iris starts installing components the owner did
not write and did not read.

## Shape

One long-lived child process per untrusted component. The host speaks JSON-RPC
over stdio. The child receives the Phase 1 types, which are already frozen and
JSON-serializable, and returns the same types. `llm`, `memory`, `files`, and
`state` are host-side proxies: the child asks, the host checks the grant, the
host performs the call.

The child is the jailed check process, kept alive. On Linux that is Landlock,
a seccomp filter, and a network namespace. On Windows the equivalent is a
restricted token and a job object, or a WASM runtime if the component can be
compiled to it. macOS stays audit-only until it has a comparable jail, and
`iris evolve` already refuses there unless `--allow-audit-isolation` is set.

## Cost

Every model call and every memory search pays a serialization round trip.
A context stage that searches once per turn can afford it. A memory backend
that embeds every chunk at index time cannot, unless the embedder stays in
the child and only the vectors cross back.

## What would make this worth building

- `iris components add` accepts a git URL the owner has not read line by line.
- A published catalog exists that is not this repository.
- The capability context has been bypassed, in a test, by a component that
  imported `iris_ai.agent.runtime` directly.

Until then the protection is the staging folder, the jailed check, the digest
pin, and the owner's approval.
