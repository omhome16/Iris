"""CLI and webhook channels stay in-process. Telegram-local never dials."""

from __future__ import annotations

import asyncio

from iris_ai.components import list_options
from iris_ai.plug import component_folder, construct, load_class
from iris_ai.testing import channel_conformance


def test_channels_are_listed_and_pass_the_suite() -> None:
    names = list_options("channel")
    assert "cli" in names
    assert "webhook" in names
    assert "telegram-local" in names
    assert "telegram" in names
    assert "http" not in names
    assert "terminal" not in names
    for name in ("cli", "webhook", "telegram-local"):
        folder = component_folder("channel", name)
        assert folder is not None
        component = construct(load_class(folder), None, trust="local")
        problems = asyncio.run(channel_conformance(component))
        assert problems == [], (name, problems)
