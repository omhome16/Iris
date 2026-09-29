"""Capabilities — the swappable parts behind the kernel.

The kernel is fixed; everything it talks to is a capability
behind a small Protocol, discovered through a registry. Each module here owns one
*kind* and does three things:

1. declares the Protocol (what the kernel may rely on),
2. holds the registry for that kind (entry-point + config discovery),
3. registers the core implementations that ship with Iris.

Imports are deliberately **not** eager: `import iris_ai.capabilities` must stay
cheap, because a capability module pulls its heavy dependency (litellm, asyncpg,
the typesafe SDK). Import the one you need.

These seams wrap the existing implementations with **no behavior change**: the
implementations themselves live behind the Protocols, reached through the
registries, so a second implementation is a registration rather than an edit.
"""

from __future__ import annotations

__all__ = []
