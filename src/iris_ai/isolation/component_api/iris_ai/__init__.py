"""The package a hosted component is allowed to import.

This is not the Iris kernel. `iris_ai.config`, `iris_ai.agent`, and the runtime
are not here. The child process puts this directory on `sys.path` and does not
put the real package there.
"""

__version__ = "0.6.0"
