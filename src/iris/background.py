"""Tracked fire-and-forget tasks.

Why this exists: Iris's post-reply passes have two very different contracts, and
treating them the same cost real latency.

- The **capture judgment** decides whether the turn taught her something, and
  the trace reports it — it must finish before the turn is done.
- The **reflection pass** (hallucination triage) only appends to
  `config/hallucination_flags.jsonl`. It cannot change the reply, the memory, or
  the trace's meaning — yet it was awaited on the reply path, so every
  retrieval-backed turn waited on an extra cheap-tier completion (~2-6 s) before
  the graph returned. Its own docstring already claimed it was not on the reply
  path.

Backgrounding it fixes that, but a bare `asyncio.create_task` is a bug waiting
to happen: the event loop holds only a weak reference, so a task with no strong
reference can be garbage-collected mid-flight (this repo already shipped that
bug once in the Telegram reconnect path). `spawn` keeps strong references and
exposes `drain()` so shutdown and tests can wait for in-flight work instead of
racing it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

log = logging.getLogger("iris.background")

# Strong references: without these the loop may GC a task before it runs.
_tasks: set[asyncio.Task] = set()


def spawn(coro: Coroutine[Any, Any, Any], *, name: str = "iris-bg") -> asyncio.Task | None:
    """Run `coro` in the background, returning its task (or None if it cannot).

    Returns None when there is no running loop or the coroutine was refused —
    callers treat that as "the background path is unavailable", never as an
    error worth failing a turn over.
    """
    try:
        task = asyncio.get_running_loop().create_task(coro, name=name)
    except RuntimeError:
        coro.close()  # never leave an un-awaited coroutine warning behind
        log.debug("background task refused: no running event loop")
        return None
    _tasks.add(task)
    task.add_done_callback(_forget)
    return task


def _forget(task: asyncio.Task) -> None:
    _tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        # Swallowed on purpose: a background pass must not surface as a failed
        # turn. It is logged loudly instead.
        log.warning("background task %s failed: %s", task.get_name(), exc)


def pending() -> int:
    """Number of in-flight background tasks (shutdown + tests)."""
    return len(_tasks)


async def drain(timeout: float | None = None) -> int:
    """Wait for in-flight tasks; returns how many are still unfinished.

    Called on shutdown and by tests that assert on background effects. A
    timeout leaves stragglers running rather than raising: shutting down
    cleanly matters more than waiting forever on a slow provider.
    """
    from iris.config import settings

    tasks = list(_tasks)
    if not tasks:
        return 0
    _done, still_running = await asyncio.wait(
        tasks, timeout=timeout if timeout is not None else settings.background_drain_timeout
    )
    if still_running:
        log.warning("%d background task(s) still running after drain timeout", len(still_running))
    return len(still_running)
