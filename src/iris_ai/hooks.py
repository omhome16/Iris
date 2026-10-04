"""Lifecycle hooks — the middleware bus a turn flows through.

The pre-tool guard chain is the *first* hook Iris ever had; it just lives inline
in the agent loop as a special case. This module generalizes it into a named,
ordered event bus, so policy, audit and instrumentation can be attached to a
turn without editing the loop that runs it — the extension seam Claude Code's
hook system provides, scoped to what this harness actually needs.

Contract:

- Hooks are ordered by ``priority`` (lower runs first, ties keep insertion
  order), so a policy hook can always pre-empt an audit hook.
- A hook may be sync or async; either is awaited uniformly.
- **A hook that raises is logged and skipped, never propagated.** Hooks are
  policy and telemetry; a broken one must not cost a reply. This is the same
  rule the guard chain and `turnlog` already follow.
"""

from __future__ import annotations

import importlib.metadata as metadata
import inspect
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("iris.hooks")

#: Canonical lifecycle events. The single source of truth — a hook may only
#: subscribe to one of these, so a typo fails at registration, not at 3 a.m.
EVENTS: tuple[str, ...] = ("turn_start", "pre_tool", "post_tool", "on_error", "turn_end")

#: The entry-point group a hook plugin advertises itself under. Its value is a
#: callable that takes the bus and subscribes to it — the plugin decides which
#: events it wants, and can read settings while doing so.
HOOK_GROUP = "iris_ai.hooks"


class HookBus:
    """An ordered set of hooks per event, emitted synchronously in order."""

    def __init__(self) -> None:
        self._hooks: dict[str, list[tuple[int, int, str, Callable[..., Any]]]] = {
            event: [] for event in EVENTS
        }
        self._seq = 0

    def on(
        self,
        event: str,
        handler: Callable[..., Any],
        *,
        priority: int = 0,
        name: str = "",
    ) -> Callable[..., Any]:
        """Subscribe a handler to an event. Returns the handler for decoration."""
        if event not in self._hooks:
            raise ValueError(f"unknown hook event {event!r} — expected one of {', '.join(EVENTS)}")
        self._seq += 1
        self._hooks[event].append(
            (priority, self._seq, name or getattr(handler, "__name__", "hook"), handler)
        )
        self._hooks[event].sort(key=lambda h: (h[0], h[1]))
        return handler

    def has(self, event: str) -> bool:
        return bool(self._hooks.get(event))

    def subscribers(self, event: str) -> list[str]:
        return [h[2] for h in self._hooks.get(event, [])]

    def describe(self) -> dict[str, list[str]]:
        """Subscriber names per event, in the order they will run."""
        return {event: self.subscribers(event) for event in EVENTS}

    async def emit(self, event: str, **payload: Any) -> list[Any]:
        """Run every hook for an event in order; collect non-None results.

        A raising hook is logged and skipped. Results are returned so a caller
        that wants a verdict from a policy hook can read it, while audit hooks
        that return nothing impose no contract.
        """
        results: list[Any] = []
        for _, _, name, handler in self._hooks.get(event, []):
            try:
                out = handler(**payload)
                if inspect.isawaitable(out):
                    out = await out
            except Exception as exc:  # noqa: BLE001 - telemetry must never cost a reply
                log.warning("hook %s/%s failed: %s", event, name, exc)
                continue
            if out is not None:
                results.append(out)
        return results

    async def emit_policy(self, event: str, **payload: Any) -> list[Any]:
        """Run policy hooks. A handler that raises refuses the call.

        Observer events stay on `emit`, which logs and skips. Policy can only
        tighten, so a broken policy hook must not let the tool run.
        """
        from iris_ai.guards import Verdict

        results: list[Any] = []
        for _, _, name, handler in self._hooks.get(event, []):
            try:
                out = handler(**payload)
                if inspect.isawaitable(out):
                    out = await out
            except Exception as exc:  # noqa: BLE001 - fail closed, do not propagate
                log.warning("policy hook %s/%s failed closed: %s", event, name, exc)
                results.append(
                    Verdict(
                        allowed=False,
                        guard=f"hook:{name}",
                        reason=f"policy hook {name} failed: {exc}",
                    )
                )
                continue
            if out is not None:
                results.append(out)
        return results


def discover_hooks(bus: HookBus) -> list[str]:
    """Attach installed hook plugins to a bus; returns the names that attached.

    A plugin publishes a callable under `iris_ai.hooks`, and boot calls it with
    the bus. That shape rather than a declarative list because a hook often needs
    more than a subscription — reading settings, building a client, keeping its
    own state — and a plugin that can run *code* at attach time is the same kind
    of extension as a channel rather than a second mechanism to document.

    A plugin that fails to load or attach is logged and skipped: telemetry and
    policy add-ons must never be the reason a harness does not boot.
    """
    attached: list[str] = []
    try:
        entry_points = list(metadata.entry_points(group=HOOK_GROUP))
    except Exception as exc:  # noqa: BLE001 - broken metadata must not stop boot
        log.warning("hook discovery failed (%s)", exc)
        return attached
    for ep in entry_points:
        try:
            attach = ep.load()
            attach(bus)
        except Exception as exc:  # noqa: BLE001 - a broken plugin is skipped, not fatal
            log.warning("hook plugin %r could not attach (%s)", ep.name, exc)
            continue
        attached.append(ep.name)
    return attached
