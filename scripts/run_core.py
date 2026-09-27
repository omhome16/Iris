"""Local dev runner for iris-core (Windows-safe).

uvicorn's asyncio default on Windows is the ProactorEventLoop, which psycopg
async cannot use. This runner hands uvicorn an explicit SelectorEventLoop.
In Docker (Linux) the plain `uvicorn iris_ai.api:app` command works unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import selectors
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

from uvicorn import Config, Server

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main() -> None:
    config = Config(
        "iris_ai.api:app",
        host="0.0.0.0",
        port=8000,
        loop="none",  # we own the loop
    )
    server = Server(config)
    asyncio.run(server.serve(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))


if __name__ == "__main__":
    main()
