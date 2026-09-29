"""Iris — a personal AI assistant with a visible mind.

Memory engineering, agent graph engineering, and context engineering,
built as one production-grade system.

The library's public surface in P2 is deliberately small: `__version__`,
`harness()` and `Harness`. The boot wiring lives in `iris_ai.engine` (that name,
not `iris_ai.harness`, because a submodule and a package attribute cannot share
one) and pulls in LangGraph, asyncpg and the rest — so it is re-exported lazily,
keeping `import iris_ai` (and therefore `iris_ai.cli`) cheap.
"""

from __future__ import annotations

import os as _os

__version__ = "0.2.0"

#: The process environment as it was *before* Iris imported anything.
#:
#: `litellm` (a core dependency) loads `.env` into `os.environ` at import time,
#: which is convenient — a provider client that reads the environment finds the
#: key — and treacherous: after that import, a line in `.env` is
#: indistinguishable from a real environment variable. The manifest's precedence
#: rule is "the environment wins over the file", and this snapshot is what keeps
#: that rule meaning a genuine override (a container, CI, a shell export) rather
#: than a line `.env.example` happened to ship. Without it, `WORKSPACE_DIR` in a
#: copied `.env` silently beats a profile's `harness.toml`.
PRELOADED_ENV: frozenset[str] = frozenset(_os.environ)

__all__ = ["PRELOADED_ENV", "Harness", "__version__", "harness"]


def __getattr__(name: str):
    """Lazy re-export. Uses `importlib` deliberately: `from iris_ai import harness`
    inside this module would look the name up on the partially-initialized
    package and recurse through this same hook."""
    if name in {"harness", "Harness"}:
        import importlib

        module = importlib.import_module("iris_ai.engine")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
