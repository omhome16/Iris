# Components

Every swappable part of Iris is a component: context, memory, persona, capture,
consolidator, channel, tools, guard, hook, model, judge, or secret store. One
line in `config/harness.toml` selects it:

```toml
[components]
context = "recall-first"
```

A name in `iris components list` is a name that loads. A component that fails
to import does not stop Iris. The previous choice, or the built-in, is used,
and `iris doctor` names the fix.

## Where a component comes from

1. **Built in.** Shipped with Iris. `iris components eject context recall-first`
   copies one into a folder you can edit.
2. **Installed.** A package that advertises the `iris_ai.components` entry
   point, for example `context.graph-rag = "pkg.mod:GraphRag"`.
3. **Local.** A folder next to `config/`:

```text
components/context/graph-rag/
  component.toml
  component.py
  test_component.py
```

Local folders are imported by file path. The directory you run `iris` from
does not matter.

`component.toml` names the kind, the class (`entry = "component:GraphRag"`),
and a one-line description. The class is constructed as
`Class(ctx, **options)`. `ctx` is `iris_ai.sdk.ComponentContext`: `llm`,
`memory`, `files`, and `options`. That is the stable surface.

## Commands

```bash
uv run iris new context graph-rag          # or: iris components eject ...
uv run iris components check context graph-rag
uv run iris components use context graph-rag
```

In chat, `/reload` applies the change without leaving the process. `/components`
lists what is available. `iris components rollback context` returns to the
previous choice. Iris also rolls back on its own if a newly activated
component fails its first few calls.

## Asking Iris to build one

Say "implement this context technique in yourself." Iris writes into
`components/.staging`, checks the code in a separate process, and calls
`component_activate` only after showing you the result. That call waits for
your approval. Iris does not edit its own package, so upgrading Iris does not
conflict with a component you or Iris added.

Components run in-process with normal Python privileges. The protection is the
staging folder, the separate-process check, and your approval. Do not activate
a component you have not read.
