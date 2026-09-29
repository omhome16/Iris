"""Generic capability registry — the plug-and-play spine.

Iris already uses this pattern three times, inline and separately: the provider
table, the role pack, and the skills registry. This module makes it reusable, so
every *kind* of swappable capability (channels, tool providers, hooks, memory
backends) gets the same contract and the same two discovery sources:

1. **Installed packages** — a Python entry point under a per-kind group, so a
   third party can `pip install iris-discord` and be found with no core edit.
2. **Config** — a name/enabled list in the harness manifest, so a capability can
   be turned on, off or ordered without touching source.

Design rules, kept from the skills registry because it is the proven precedent:

- **Nothing is silently dropped.** A duplicate name is an explicit conflict
  naming both sources, never a coin flip.
- **Lookup is validated.** An unknown name fails with the list of known names,
  so a typo is a clear error instead of a capability that silently never runs.
- **Discovery is cheap.** Registering an entry point does *not* call it; the
  factory runs only for capabilities that are actually enabled. A disabled
  integration is never imported, let alone constructed.
"""

from __future__ import annotations

import importlib.metadata as metadata
import logging
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("iris.registry")


class RegistryError(RuntimeError):
    """A registry contract was violated (duplicate, unknown name, bad factory)."""


@dataclass(frozen=True, slots=True)
class Registration[T]:
    """One registered capability. Constructing it is always an explicit call."""

    kind: str
    name: str
    factory: Callable[..., T]
    source: str = "core"
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def build(self, **kwargs: Any) -> T:
        """Call the factory. The only place a capability is instantiated."""
        return self.factory(**kwargs)


class Registry[T]:
    """One registry per capability kind."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._entries: dict[str, Registration[T]] = {}

    # ── registration ─────────────────────────────────────────────────────
    def register(
        self,
        name: str,
        factory: Callable[..., T],
        *,
        source: str = "core",
        enabled: bool = True,
        replace: bool = False,
        **metadata: Any,
    ) -> Registration[T]:
        name = name.strip()
        if not name:
            raise RegistryError(f"{self.kind}: a capability needs a name")
        if not callable(factory):
            raise RegistryError(f"{self.kind}.{name}: factory is not callable")
        existing = self._entries.get(name)
        if existing is not None and not replace:
            raise RegistryError(
                f"{self.kind} conflict: {name!r} is already registered by "
                f"{existing.source!r} (new source {source!r})"
            )
        entry = Registration(
            kind=self.kind, name=name, factory=factory, source=source, enabled=enabled, metadata=dict(metadata)
        )
        self._entries[name] = entry
        return entry

    def register_value(self, name: str, value: T, **kwargs: Any) -> Registration[T]:
        """Register an already-built value behind a factory (for the core)."""
        return self.register(name, lambda: value, **kwargs)

    # ── lookup ───────────────────────────────────────────────────────────
    def __contains__(self, name: object) -> bool:
        return name in self._entries

    def names(self) -> list[str]:
        return sorted(self._entries)

    def get(self, name: str) -> Registration[T]:
        entry = self._entries.get(name)
        if entry is None:
            known = ", ".join(self.names()) or "none"
            raise RegistryError(f"unknown {self.kind} {name!r} — registered: {known}")
        return entry

    def build(self, name: str, **kwargs: Any) -> T:
        return self.get(name).build(**kwargs)

    def enabled(self) -> list[Registration[T]]:
        return [r for r in self._entries.values() if r.enabled]

    def __iter__(self) -> Iterator[Registration[T]]:
        return iter(self._entries.values())

    def __len__(self) -> int:
        return len(self._entries)

    # ── discovery ────────────────────────────────────────────────────────
    def discover(self, group: str, *, enabled: set[str] | None = None) -> list[Registration[T]]:
        """Register factories advertised under an entry-point group.

        The factory is registered, not called, so discovery never imports or
        constructs a capability that config disabled. A name that core already
        owns is left alone (core wins over a third-party collision unless the
        third party registers with `replace=True` explicitly).
        """
        found: list[Registration[T]] = []
        try:
            entry_points = list(metadata.entry_points(group=group))
        except Exception as exc:  # noqa: BLE001 - broken packaging metadata must not stop boot
            log.warning("%s: entry-point discovery failed (%s)", self.kind, exc)
            return found
        for ep in entry_points:
            if enabled is not None and ep.name not in enabled:
                continue
            if ep.name in self._entries:
                continue
            try:
                factory = ep.load()
            except Exception as exc:  # noqa: BLE001 - a broken plugin is skipped, not fatal
                log.warning("%s: could not load entry point %r (%s)", self.kind, ep.name, exc)
                continue
            self.register(ep.name, factory, source=f"entry_point:{group}:{ep.name}")
            found.append(self._entries[ep.name])
        return found
