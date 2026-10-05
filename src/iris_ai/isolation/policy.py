"""Whether an executable component may run on this platform.

Linux can apply Landlock inside the child. Windows and macOS cannot. On those
platforms an executable component stays unloaded unless the owner sets
`ALLOW_AUDIT_ISOLATION=true`, which means they accept the audit subprocess
or an in-process load. That flag does not create a kernel jail.
"""

from __future__ import annotations

import sys


def execution_refusal(kind: str) -> str:
    """Empty when the component may load. A sentence when it must not."""
    if kind not in {"context", "memory", "persona", "capture", "consolidator", "tool"}:
        return ""
    from iris_ai.config import settings

    if sys.platform.startswith("linux") or settings.allow_audit_isolation:
        return ""
    return (
        f"{kind} is executable and this platform has no kernel jail, so it was not loaded. "
        "Set ALLOW_AUDIT_ISOLATION=true to run it anyway. That is an audit boundary, not a kernel jail."
    )
