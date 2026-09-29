"""Contract tests for the plug-and-play primitives: the generic registry and
the lifecycle hook bus. These are the seams every future integration uses, so
their failure modes (duplicate names, unknown names, broken plugins, raising
hooks) are pinned here rather than discovered in production."""

from __future__ import annotations

import pytest

from iris_ai import hooks as hooks_mod
from iris_ai.registry import Registry, RegistryError


# ── registry ─────────────────────────────────────────────────────────────
def test_register_and_build():
    reg: Registry[int] = Registry("widget")
    reg.register("doubler", lambda n=1: n * 2)
    assert reg.build("doubler", n=21) == 42
    assert reg.names() == ["doubler"]


def test_unknown_name_lists_the_known_alternatives():
    reg: Registry[int] = Registry("widget")
    reg.register("alpha", lambda: 1)
    with        pytest.raises(RegistryError, match=r"unknown widget 'beta'.*registered: alpha"):
        reg.get("beta")


def test_a_duplicate_name_is_an_explicit_conflict():
    reg: Registry[int] = Registry("widget")
    reg.register("alpha", lambda: 1, source="core")
    with        pytest.raises(RegistryError, match=r"conflict.*core"):
        reg.register("alpha", lambda: 2, source="plugin")
    # Replacement is allowed only when asked for explicitly.
    reg.register("alpha", lambda: 3, source="plugin", replace=True)
    assert reg.build("alpha") == 3


def test_disabled_is_registered_but_not_enabled():
    reg: Registry[int] = Registry("widget")
    reg.register("on", lambda: 1)
    reg.register("off", lambda: 2, enabled=False)
    assert reg.names() == ["off", "on"]
    assert [r.name for r in reg.enabled()] == ["on"]


class _FakeEntryPoint:
    def __init__(self, name, factory):
        self.name = name
        self._factory = factory

    def load(self):
        return self._factory


def test_discovery_registers_without_calling_factories(monkeypatch):
    built: list[str] = []

    def factory():
        built.append("called")
        return 1

    monkeypatch.setattr(
        "iris_ai.registry.metadata.entry_points",
        lambda group: [_FakeEntryPoint("discord", factory)] if group == "iris_ai.channels" else [],
    )

    reg: Registry[int] = Registry("channel")
    found = reg.discover("iris_ai.channels")
    assert [r.name for r in found] == ["discord"]
    assert built == []  # discovery must not import or construct anything


def test_discovery_can_be_limited_to_an_enabled_set(monkeypatch):
    monkeypatch.setattr(
        "iris_ai.registry.metadata.entry_points",
        lambda group: [_FakeEntryPoint("a", lambda: 1), _FakeEntryPoint("b", lambda: 2)],
    )
    reg: Registry[int] = Registry("channel")
    reg.discover("iris_ai.channels", enabled={"b"})
    assert reg.names() == ["b"]


# ── hooks ────────────────────────────────────────────────────────────────
async def test_hooks_run_in_priority_order_and_collect_results():
    bus = hooks_mod.HookBus()
    seen: list[str] = []

    bus.on("pre_tool", lambda **_: seen.append("late"), priority=10)
    bus.on("pre_tool", lambda **_: seen.append("early"), priority=-10)
    bus.on("pre_tool", lambda **_: "verdict")
    results = await bus.emit("pre_tool", tool="x")
    assert seen == ["early", "late"]
    assert "verdict" in results


async def test_an_async_hook_is_awaited():
    bus = hooks_mod.HookBus()

    async def hook(**_):
        return "async"

    bus.on("turn_end", hook)
    assert await bus.emit("turn_end") == ["async"]


async def test_a_raising_hook_is_skipped_not_propagated():
    bus = hooks_mod.HookBus()
    seen: list[str] = []

    def boom(**_):
        raise RuntimeError("bad hook")

    bus.on("on_error", boom, priority=-1)
    bus.on("on_error", lambda **_: seen.append("survived"), priority=1)
    results = await bus.emit("on_error", error="x")
    assert seen == ["survived"]
    assert results == []


def test_an_unknown_event_is_rejected_at_registration():
    bus = hooks_mod.HookBus()
    with pytest.raises(ValueError, match="unknown hook event 'nope'"):
        bus.on("nope", lambda **_: None)


def test_a_hook_plugin_attaches_itself_to_the_bus(monkeypatch):
    """An installed plugin subscribes to the bus through `iris_ai.hooks`, so
    telemetry and policy add-ons need no core edit — the same promise the channel
    and tool registries already keep."""
    bus = hooks_mod.HookBus()

    def attach(bus_: hooks_mod.HookBus) -> None:
        bus_.on("post_tool", lambda **_: None, name="audit")

    monkeypatch.setattr(
        "iris_ai.hooks.metadata.entry_points",
        lambda group: [_FakeEntryPoint("audit-plugin", attach)] if group == hooks_mod.HOOK_GROUP else [],
    )
    assert hooks_mod.discover_hooks(bus) == ["audit-plugin"]
    assert "audit" in bus.subscribers("post_tool")
    assert bus.describe()["post_tool"] == ["audit"]


def test_a_broken_hook_plugin_is_skipped_not_fatal(monkeypatch):
    """One bad plugin must not cost the harness its boot."""
    bus = hooks_mod.HookBus()

    def bad(_bus):
        raise RuntimeError("broken plugin")

    def good(bus_: hooks_mod.HookBus) -> None:
        bus_.on("turn_end", lambda **_: None, name="good")

    monkeypatch.setattr(
        "iris_ai.hooks.metadata.entry_points",
        lambda group: [_FakeEntryPoint("bad", bad), _FakeEntryPoint("good", good)],
    )
    assert hooks_mod.discover_hooks(bus) == ["good"]
    assert "good" in bus.subscribers("turn_end")
    assert "bad" not in bus.subscribers("turn_end")
