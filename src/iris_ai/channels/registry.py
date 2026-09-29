"""Channel registry — declare channels by name, enable them from config.

This is the plug-and-play seam for transports. Core registers `telegram`; a
package can register `discord` (or anything else) through the
``iris_ai.channels`` entry-point group, and a user enables it by name in config
— no core edit, no import of a disabled transport.

Precedence for which channels are live:

    registered  →  channels_enabled (allow-list)  →  channels_disabled (deny)

An empty ``CHANNELS_ENABLED`` means "every registered channel"; ``CHANNELS_DISABLED``
always wins, so a single env var can turn one off on a shared host. URLs are
resolved per channel from ``CHANNEL_<NAME>_URL``, falling back to the
channel's own setting (Telegram keeps ``TELEGRAM_MCP_URL`` so existing
installs are unchanged).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from iris_ai.channels.base import Channel
from iris_ai.channels.telegram_mcp import TelegramMCPClient
from iris_ai.config import settings
from iris_ai.registry import Registry

log = logging.getLogger("iris.channels")


@dataclass(frozen=True, slots=True)
class ChannelSpec:
    """One channel the config asks for, resolved to a concrete address."""

    name: str
    url: str = ""
    timeout_s: float = 10.0


#: The channel registry. Core owns `telegram`; `discover()` adds installed ones.
CHANNELS: Registry[Channel] = Registry("channel")


def _telegram_factory(*, url: str = "", timeout_s: float = 10.0) -> Channel:
    return TelegramMCPClient(url or settings.telegram_mcp_url, name="telegram", timeout_s=timeout_s)


CHANNELS.register("telegram", _telegram_factory, source="core")


def discover_channels(enabled: set[str] | None = None) -> list[str]:
    """Register installed channel plugins. Returns the names that were added."""
    found = CHANNELS.discover("iris_ai.channels", enabled=enabled)
    return [r.name for r in found]


def _split(raw: str) -> set[str]:
    return {part.strip() for part in raw.replace(";", ",").split(",") if part.strip()}


def _channel_url(name: str) -> str:
    from_env = os.environ.get(f"CHANNEL_{name.upper()}_URL", "")
    if from_env:
        return from_env
    if name == "telegram":
        return settings.telegram_mcp_url
    return ""


def channel_specs() -> list[ChannelSpec]:
    """The channels config asks for, in registry order, with URLs resolved."""
    allow = _split(settings.channels_enabled)
    deny = _split(settings.channels_disabled)
    specs: list[ChannelSpec] = []
    for reg in CHANNELS.enabled():
        if allow and reg.name not in allow:
            continue
        if reg.name in deny:
            continue
        specs.append(
            ChannelSpec(
                name=reg.name,
                url=_channel_url(reg.name),
                timeout_s=settings.channel_connect_timeout_s,
            )
        )
    return specs


def build_channel(spec: ChannelSpec) -> Channel:
    """Construct one channel from the registry. Never called for a disabled one."""
    return CHANNELS.build(spec.name, url=spec.url, timeout_s=spec.timeout_s)


async def connect_channels(specs: list[ChannelSpec] | None = None) -> dict[str, Channel]:
    """Build and connect every configured channel. A down channel is skipped.

    Boot never fails because one transport is unreachable — the same contract
    the Telegram retry task already honoured, now applied to every channel.
    """
    specs = channel_specs() if specs is None else specs
    connected: dict[str, Channel] = {}
    for spec in specs:
        channel = build_channel(spec)
        if await channel.connect():
            connected[spec.name] = channel
            log.info("channel connected: %s", spec.name)
        else:
            log.warning("channel unavailable at boot: %s (%s)", spec.name, spec.url)
    return connected
