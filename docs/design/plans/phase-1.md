# Phase 1 — contracts v1

1. `iris_ai.sdk` is a package. Types round-trip through JSON. `ContextResult.render()` joins blocks as `## Title\ntext`.
2. `ComponentContext(runtime, options)` stays v0 and warns once. v1 sets `runtime` to None and fills only granted capabilities.
3. `construct` reads `api_version` and `permissions` off the class (set by `load_class` from the manifest).
4. The resolver order is built-in, local, `iris_ai.components` entry point, dotted path.
5. Lock v2 migrates `{kind: {active, previous, fails}}`. Digest drift refuses the load. First use pins.
6. `iris components inspect` prints source, permissions, pinned digest, current digest.
7. Personas are cached and wrapped in `Guarded`.
8. `check_folder` probes v1 return types.
9. `iris_ai.testing` exposes `fake_context`, `sample_request`, `HashEmbedder`, `memory_conformance`.
