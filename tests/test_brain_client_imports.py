"""The brain-client module must stay importable without the engine.

The Telegram bridge installs the package with `pip install --no-deps .`, so if
`iris.channels.brain` pulled in LangGraph, asyncpg or the memory layer, the
bridge image would silently need the whole engine — the dependency rule in the
P3 spec, enforced rather than documented.
"""

from __future__ import annotations

import json
import subprocess
import sys

FORBIDDEN = ["langgraph", "asyncpg", "numpy", "iris.agent", "iris.memory", "iris.jev", "iris.engine"]

_CODE = """
import json, sys
import iris.channels.brain  # noqa: F401
forbidden = {forbidden!r}
loaded = sorted(
    m for m in sys.modules if any(m == f or m.startswith(f + ".") for f in forbidden)
)
print(json.dumps(loaded))
"""


def test_brain_module_imports_without_the_engine():
    proc = subprocess.run(
        [sys.executable, "-c", _CODE.format(forbidden=FORBIDDEN)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    loaded = json.loads(proc.stdout.strip().splitlines()[-1])
    assert loaded == [], f"iris.channels.brain dragged in the engine: {loaded}"
