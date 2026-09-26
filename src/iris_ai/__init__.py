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

__version__ = "0.1.0"

__all__ = ["Harness", "__version__", "harness"]


def __getattr__(name: str):
    """Lazy re-export. Uses `importlib` deliberately: `from iris_ai import harness`
    inside this module would look the name up on the partially-initialized
    package and recurse through this same hook."""
    if name in {"harness", "Harness"}:
        import importlib

        module = importlib.import_module("iris_ai.engine")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
