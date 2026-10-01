"""The protocol half of the MCP pool: one connection per declared server.

Small on purpose. The SDK already speaks the protocol; what this file adds is the
three things a *harness* needs from it:

- **One entry point, one failure mode.** `open_server` either returns a connected
  session or raises `McpUnavailable` naming the server. A declared server that is
  down is a degraded capability, never a boot failure and never a half-connected
  client that fails later at the first tool call.
- **Results shaped like Iris's own tools.** Every call returns a JSON object with
  `ok`, so a server-reported error is visible as a failure instead of arriving as
  plausible-looking prose — the same contract `iris_ai.agent.tools` holds itself
  to. Nothing here interprets the *content*: an MCP server's output is untrusted
  data, and screening it is a separate, later concern.
- **`server=` as a seam.** Passing an already-built SDK server connects
  in-process. That is how the tests reach a real handshake with no subprocess and
  no port, and it is the seam a plugin would use to contribute a server it hosts
  itself.

See `iris_ai.mcp` for the trust model and for why the stdio transport cannot run
under the CLI's event loop on Windows.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

try:  # MCP servers are the [mcp] extra
    from mcp.client import Client
    from mcp.client.sse import sse_client
    from mcp.client.stdio import StdioServerParameters, stdio_client
except ImportError:  # pragma: no cover
    Client = None  # type: ignore[assignment,misc]
    sse_client = None  # type: ignore[assignment]
    StdioServerParameters = None  # type: ignore[assignment,misc]
    stdio_client = None  # type: ignore[assignment]

from iris_ai.mcp import McpServerSpec, McpToolInfo, namespaced

log = logging.getLogger("iris.mcp")


class McpUnavailable(RuntimeError):
    """A declared server could not be reached. Named, and never fatal to a caller.

    Reachability is the mutable kind of failure — the server may simply not have
    started yet — which is why the pool is allowed to retry this one.
    """


class McpUnsupported(McpUnavailable):
    """This server cannot be reached *by construction* — retrying will not help.

    A distinct type because the difference is a behaviour, not a message: a
    stdio server on a loop that cannot spawn processes is not "down", and a pool
    that kept retrying it would fill the log with a fact that will never change.
    """


def _stdio_blocked() -> bool:
    """Whether this process's event loop can spawn a server at all.

    The SDK's stdio transport spawns the server with asyncio subprocesses, and
    Windows' *selector* loop does not implement them — the same wall
    `skills/runner.py` hit, which is why skill scripts run `subprocess.run` on a
    worker thread instead. `iris chat` and `iris api` select that loop on Windows
    (psycopg's async path needs it), so without this check a stdio server declared
    there would fail deep inside the SDK with a bare `NotImplementedError` that
    says nothing about which server or what to do.

    Checked at the moment of connecting rather than at import: the loop in force
    is the loop that matters, and a library caller is free to have chosen another.
    """
    if sys.platform != "win32":
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover - every caller of this module is async
        return False
    return isinstance(loop, asyncio.SelectorEventLoop)


def _target(spec: McpServerSpec) -> Any:
    """What the SDK is handed for this spec: a URL, an SSE stream pair, or stdio."""
    if spec.transport == "http":
        return spec.url
    if spec.transport == "sse":
        return sse_client(spec.url)
    return stdio_client(
        StdioServerParameters(
            command=spec.command,
            args=list(spec.args),
            # `None`, not `{}`: the SDK layers these over the inherited minimal
            # environment rather than replacing it, so an empty mapping and an
            # absent one mean the same thing and only one of them is honest
            # about that.
            env=dict(spec.env) or None,
            cwd=spec.cwd or None,
        )
    )


def _info(tool: Any) -> McpToolInfo:
    annotations = getattr(tool, "annotations", None)
    return McpToolInfo(
        name=str(tool.name),
        description=str(getattr(tool, "description", "") or "").strip(),
        input_schema=dict(getattr(tool, "input_schema", None) or {}),
        read_only=bool(getattr(annotations, "read_only_hint", False)),
        destructive=bool(getattr(annotations, "destructive_hint", False)),
    )


@dataclass(slots=True)
class McpConnection:
    """One live MCP session. `close()` releases the transport and is idempotent."""

    spec: McpServerSpec
    client: Client
    _stack: AsyncExitStack
    _closed: bool = False

    async def tools(self) -> list[McpToolInfo]:
        """Everything the server advertises, in the order it advertises it."""
        listing = await self.client.list_tools()
        return [_info(tool) for tool in listing.tools]

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> str:
        """Call one tool and render the reply as JSON for whoever asked."""
        return json.dumps(await self.call_raw(tool, arguments), ensure_ascii=False)

    async def call_raw(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call one tool and return the reply as a dict.

        An exception from the transport and a failure the server *reported* both
        come back as `{"ok": false, ...}`: the caller is a tool boundary, and a
        boundary that raises hides the reason from the model that could act on it.

        `call()` renders this as JSON for the model. The dict form exists because
        a caller may need to *inspect* the reply first — the pool screens a
        screened server's text for instruction injection, and screening a
        serialized string would mean parsing it again to change one field.
        """
        try:
            result = await self.client.call_tool(tool, arguments or {})
        except Exception as exc:  # noqa: BLE001 - a transport failure is a result here
            return {
                "ok": False,
                "tool": namespaced(self.spec.name, tool),
                "error": f"{type(exc).__name__}: {exc}",
            }

        text = "\n".join(
            str(getattr(block, "text", "")) for block in result.content if getattr(block, "text", None)
        ).strip()
        payload: dict[str, Any] = {"ok": not bool(result.is_error), "tool": namespaced(self.spec.name, tool)}
        if text:
            payload["text"] = text
        if result.structured_content is not None:
            payload["structured"] = result.structured_content
        if not text and result.structured_content is None:
            # Non-text content (an image, a resource link): name what arrived
            # rather than pretend the call returned nothing.
            payload["content_types"] = [type(block).__name__ for block in result.content]
        if result.is_error:
            payload["error"] = text or "the server reported a failed call"
        return payload

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._stack.aclose()


class _ProactorConnection:
    """An MCP session that lives on a proactor loop in another thread."""

    def __init__(self, spec: McpServerSpec, loop: asyncio.AbstractEventLoop, inner: McpConnection) -> None:
        self.spec = spec
        self.client = inner.client
        self._loop = loop
        self._inner = inner

    async def _hop(self, coro):
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, self._loop))

    async def tools(self) -> list[McpToolInfo]:
        return await self._hop(self._inner.tools())

    async def call(self, tool: str, arguments: dict[str, Any] | None = None) -> str:
        return await self._hop(self._inner.call(tool, arguments))

    async def call_raw(self, tool: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._hop(self._inner.call_raw(tool, arguments))

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            await self._hop(self._inner.close())
        self._loop.call_soon_threadsafe(self._loop.stop)


async def _connect_on_proactor(spec: McpServerSpec, timeout: float) -> _ProactorConnection:
    """Spawn a stdio server on a proactor loop. The selector loop cannot."""
    policy = asyncio.WindowsProactorEventLoopPolicy()
    loop = policy.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, name=f"iris-mcp-{spec.name}", daemon=True)
    thread.start()

    async def _open() -> McpConnection:
        stack = AsyncExitStack()
        client = await stack.enter_async_context(
            Client(_target(spec), read_timeout_seconds=timeout)
        )
        return McpConnection(spec=spec, client=client, _stack=stack)

    try:
        inner = await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(_open(), loop))
    except Exception:
        loop.call_soon_threadsafe(loop.stop)
        raise
    return _ProactorConnection(spec, loop, inner)


@asynccontextmanager
async def open_server(
    spec: McpServerSpec, *, server: Any | None = None, timeout: float = 20.0
) -> AsyncIterator[McpConnection]:
    """Connect to one declared server, or raise `McpUnavailable` naming it.

    A context manager rather than a plain coroutine, so the transport is released
    on every exit path including the ones a caller did not plan for. A caller
    that needs it for the process's lifetime enters it on its own exit stack —
    which is how an engine-held connection is meant to be kept.

    `server` connects an in-process SDK server instead of the spec's transport
    (the test seam, and the plugin seam). Everything else — a spawn failure, a
    refused URL, a failed handshake — is the same outcome to the caller, which is
    what "degraded, not fatal" requires.
    """
    if Client is None or stdio_client is None or sse_client is None:
        raise McpUnavailable(f"{spec.name}: MCP needs the mcp extra: uv sync --extra mcp")
    if server is None and spec.transport == "stdio" and _stdio_blocked():
        try:
            connection = await _connect_on_proactor(spec, timeout)
        except Exception as exc:
            raise McpUnavailable(
                f"{spec.name}: stdio could not start on the Windows proactor loop "
                f"({type(exc).__name__}: {exc}). The selector loop cannot spawn "
                "processes. Use an http server (`iris mcp add custom --transport http`) "
                "or run the server yourself and set a url."
            ) from exc
        try:
            yield connection
        finally:
            await connection.close()
        return
    stack = AsyncExitStack()
    try:
        client = await stack.enter_async_context(
            Client(server if server is not None else _target(spec), read_timeout_seconds=timeout)
        )
    except BaseException as exc:  # connect/spawn/handshake are one outcome to a caller
        with contextlib.suppress(Exception):
            await stack.aclose()
        if isinstance(exc, asyncio.CancelledError):
            raise
        raise McpUnavailable(f"{spec.name}: {type(exc).__name__}: {exc}") from exc
    log.info("mcp connected: %s (%s)", spec.name, spec.transport)
    connection = McpConnection(spec=spec, client=client, _stack=stack)
    try:
        yield connection
    finally:
        await connection.close()
