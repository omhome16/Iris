"""The setup wizard opens on the provider step and moves forward."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("textual")


async def test_wizard_starts_on_provider_and_advances(tmp_path: Path):
    from iris_ai.cli.tui.wizard import WizardApp

    app = WizardApp(tmp_path)
    async with app.run_test() as pilot:
        seen = ""
        for _ in range(30):
            seen = str(app.query_one("#status").render())
            if "provider" in seen:
                break
            await pilot.pause(0.05)
        else:
            raise AssertionError(seen)
        assert app.query("#provider")
        await pilot.click("#next")
        await pilot.pause(0.1)
        assert "key" in str(app.query_one("#status").render())
        assert app.query("#api_key")
