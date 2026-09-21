"""Local dev runner for the Telegram MCP bridge.

The bridge reads TELEGRAM_BOT_TOKEN / IRIS_CORE_URL from the environment;
`uv run` and plain python do not load .env automatically, so this runner
loads it (dotenv-style, no external dep) before booting the server.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BRIDGE_DIR = ROOT / "mcp_servers" / "telegram"
sys.path.insert(0, str(BRIDGE_DIR))
sys.path.insert(0, str(ROOT / "src"))


def _load_env() -> None:
    env_file = ROOT / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in __import__("os").environ:
            __import__("os").environ[key] = value


if __name__ == "__main__":
    _load_env()
    import asyncio

    import server

    asyncio.run(server.main())
