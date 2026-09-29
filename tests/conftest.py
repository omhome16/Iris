"""Suite-wide isolation from the machine the tests happen to run on.

One fixture, because there is exactly one setting that reads a file the
*developer* owns rather than the repo: `MCP_SERVERS_FILE` defaults to `.mcp.json`
in the working directory, and `.mcp.json` is gitignored on purpose (it names
commands, URLs and env vars, so it is per-machine). Without this, `pytest` in a
checkout whose owner has declared three MCP servers would try to connect to them
during every harness-boot test — slow, flaky, and dependent on local state that
CI does not have. The tests that are *about* declared servers set the setting
themselves, and a fixture's own `monkeypatch.setattr` runs after this one, so they
win.
"""

from __future__ import annotations

import pytest

from iris_ai.config import settings


@pytest.fixture(autouse=True)
def isolate_mcp_config(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """No declared servers unless a test declares some."""
    monkeypatch.setattr(settings, "mcp_servers_file", str(tmp_path / "no-servers.json"))
