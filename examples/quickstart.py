"""Ten lines: a provider, a key from the environment, one reply.

    uv run python examples/quickstart.py
"""

import asyncio
import os

import iris_ai


async def main() -> None:
    async with iris_ai.harness(
        provider="groq",
        model="openai/gpt-oss-120b",
        api_key=os.environ["GROQ_API_KEY"],
    ) as iris:
        print(await iris.respond("Say hello in one sentence."))


if __name__ == "__main__":
    asyncio.run(main())
