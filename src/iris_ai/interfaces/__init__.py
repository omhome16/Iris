"""Protocol surfaces — each one a *client of the kernel*, never a second brain.

`iris_ai.cli` and `iris_ai.api` are the two that shipped first. This package is
where a new wire protocol goes when it is thin enough to be a mapping and not a
reimplementation: it opens the same `Harness` the CLI opens, and the turn,
memory, approval, budget and trace behavior it exposes is the kernel's, not its
own.

Nothing here is imported by `import iris_ai`, and an adapter with an optional
dependency (ACP) imports it lazily, so the harness installs and runs without it.
"""
