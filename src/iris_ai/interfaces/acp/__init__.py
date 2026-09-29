"""The editor surface: Iris over the Agent Client Protocol.

`Agent Client Protocol <https://agentclientprotocol.com>`_ is the "LSP for
agents" — an editor (Zed, JetBrains, anything that speaks ACP) drives a
conforming agent over JSON-RPC on stdio. `IrisAcpAgent` is the adapter;
`iris_ai.interfaces.acp.serve` is the process an editor spawns.

The `agent-client-protocol` dependency is an optional extra
(``pip install "iris-personal-ai[acp]"``), so importing this package without it
raises an error that says exactly that rather than an ImportError from three
frames down.
"""

from __future__ import annotations

__all__ = ["THREAD_PREFIX", "IrisAcpAgent"]


def __getattr__(name: str):
    """Lazy, for the same reason `iris_ai.cli` defers `engine`: `import iris_ai`
    should not pull an optional dependency (or LangGraph) in."""
    if name in __all__:
        import importlib

        module = importlib.import_module("iris_ai.interfaces.acp.agent")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
