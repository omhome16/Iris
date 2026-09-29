"""`iris-acp` — the process an editor spawns.

An ACP agent is a child process the editor talks to over stdin/stdout, so this
module's whole job is: boot the same harness every other client boots, wrap it
in `IrisAcpAgent`, and hand the streams to the protocol's own runner.

Two things here are load-bearing rather than tidy:

**stdout belongs to the protocol.** One stray `print` corrupts the JSON-RPC
stream and the editor reports a parse error with no clue where it came from.
Iris uses rich consoles elsewhere, so the streams are taken *first* and stdout
is then redirected to stderr for the rest of the process — the wire keeps its
own writer while anything that would have printed lands in the editor's log.

**The boot is the harness's, not this file's.** `services=True` starts the
scheduler and channels, and Postgres is `auto`: a machine with no database gets
the SQLite checkpointer and a degraded-but-working memory rather than a process
that refuses to start. Any boot failure is reported on stderr and exits
non-zero, which is what an editor shows as "the agent failed to start".

No Windows event-loop policy is set here on purpose (unlike `iris chat`). The
protocol's Windows stdio transport needs `connect_write_pipe`, which
`WindowsSelectorEventLoopPolicy` does not implement — so on Windows the loop
must stay the default Proactor one. The consequence, recorded in
`docs/acp.md`, is that an ACP session on Windows uses the SQLite checkpointer
even when Postgres is configured.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys

log = logging.getLogger("iris.acp")


async def serve() -> int:
    """Boot, serve until the client disconnects, shut down cleanly."""
    from acp import run_agent
    from acp.stdio import stdio_streams

    from iris_ai.engine import harness
    from iris_ai.interfaces.acp.agent import IrisAcpAgent

    # Take the streams before redirecting: `run_agent` would otherwise create
    # them from the redirected stdout and talk into stderr.
    reader, writer = await stdio_streams()

    async with harness(services=True) as brain:
        log.info(
            "iris-acp ready (mode=%s, checkpointer=%s, memory=%s)",
            brain.mode,
            brain.checkpointer or "unknown",
            type(brain.index).__name__,
        )
        agent = IrisAcpAgent(brain)
        with contextlib.redirect_stdout(sys.stderr):
            await run_agent(agent, input_stream=writer, output_stream=reader)
    return 0


def main() -> int:
    """Entry point for the `iris-acp` console script."""
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(name)s %(message)s")
    try:
        return asyncio.run(serve())
    except KeyboardInterrupt:
        # The editor closing the pipe is not a failure.
        return 0
    except Exception as exc:  # noqa: BLE001 — a stdio agent must say why it died
        log.error("iris-acp could not start: %s: %s", type(exc).__name__, exc)
        print(
            "hint: `iris doctor` checks the environment; `iris-acp` needs "
            'pip install "iris-personal-ai[acp]"',
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover - manual/editor entry
    sys.exit(main())
