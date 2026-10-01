"""Send harness logs to a file instead of the chat console.

Provider failover used to print a multi-line exception into the same terminal
the owner was typing in. The chat surface shows a one-line status; the detail
lives in `workspace/logs/iris.log`.
"""

from __future__ import annotations

import logging
from pathlib import Path

_CONFIGURED = False


def configure_logging(workspace: Path) -> Path:
    """Attach a file handler to the `iris` and LiteLLM loggers. Idempotent."""
    global _CONFIGURED
    log_dir = Path(workspace) / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "iris.log"
    if _CONFIGURED:
        return path
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    for name in ("iris", "iris.llm", "iris.graph", "iris.kernel", "LiteLLM", "litellm"):
        logging.getLogger(name).addHandler(handler)
    # The chat console should not see provider banners. Other `iris.*` loggers
    # still propagate, so a test can capture them.
    for name in ("iris.llm", "LiteLLM", "litellm"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    _CONFIGURED = True
    return path
