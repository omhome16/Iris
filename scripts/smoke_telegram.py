"""Smoke test: boot iris-core + telegram bridge together, verify the MCP channel.

Run: uv run python scripts/smoke_telegram.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

# uv run does not inject .env into os.environ; the bridge reads os.environ.
env_file = Path(__file__).resolve().parents[1] / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_servers" / "telegram"))

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")
log = logging.getLogger("smoke-telegram")


async def main() -> None:
    # 1. Start the bridge in-process (it also starts polling Telegram)
    import server as bridge

    bridge_task = asyncio.create_task(bridge.main())

    # 2. Connect an MCP client to it, like iris-core would
    from mcp.client.client import Client

    url = os.environ.get("TELEGRAM_MCP_URL", "http://127.0.0.1:8100/mcp")
    for attempt in range(20):
        try:
            client = Client(url)
            await client.__aenter__()
            tools = await client.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("MCP tools:", names)
            assert "send_message" in names and "get_chat_history" in names and "broadcast" in names
            print("MCP handshake OK")
            await client.__aexit__(None, None, None)
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == 19:
                raise
            log.warning("retry (%d): %s", attempt, exc)
            await asyncio.sleep(1)

    # 3. Verify the bot identity via the bridge's own API helper
    me = await bridge._tg("getMe")
    print("bot:", json.dumps(me, indent=2))
    assert me and me.get("is_bot")

    bridge_task.cancel()
    print("SMOKE OK")


if __name__ == "__main__":
    asyncio.run(main())