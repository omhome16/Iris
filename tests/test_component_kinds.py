"""Engine, channel, tool, and guard are folder components. Unknown engines fail."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from iris_ai.components import list_options
from iris_ai.config import settings
from iris_ai.engines.dispatch import resolve
from iris_ai.plug import check_folder, scaffold
from iris_ai.registry import RegistryError


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = tmp_path / "config"
    config.mkdir()
    harness = config / "harness.toml"
    harness.write_text("[components]\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_config", str(harness))


def test_an_unknown_engine_fails_with_the_known_list() -> None:
    react = resolve(None)
    assert react.name == "react"
    assert resolve("").name == "react"
    assert resolve("react").streams() is True
    assert resolve("plan-execute").name == "plan-execute"
    with pytest.raises(RegistryError, match="unknown engine 'typo'") as exc:
        resolve("typo")
    message = str(exc.value)
    assert "react" in message
    assert "plan-execute" in message
    with pytest.raises(RegistryError, match="unknown engine"):
        resolve("no.such.module:Missing")


def test_a_dotted_engine_is_loaded_instead_of_react(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = tmp_path / "dotted_engine.py"
    module.write_text(
        "class Yes:\n"
        "    name = 'yes'\n"
        "    def short_circuit(self, state):\n"
        "        return None\n"
        "    def wants_plan(self, state):\n"
        "        return False\n"
        "    def streams(self):\n"
        "        return False\n"
        "    def tool_phase(self):\n"
        "        return {}\n"
        "    def finish(self, graph, done):\n"
        "        return None\n"
        "    def after_tools(self, state):\n"
        "        return None\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    sys.modules.pop("dotted_engine", None)
    engine = resolve("dotted_engine:Yes")
    assert engine.name == "yes"
    assert engine.streams() is False


def test_new_engine_folder_loads_and_is_listed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    folder = scaffold("engine", "my-engine", root=tmp_path / "components")
    ok, detail = check_folder(folder)
    assert ok, detail
    assert "my-engine" in list_options("engine")
    engine = resolve("my-engine")
    assert engine.streams() is True
    assert engine.wants_plan({}) is False


def test_channel_tool_and_guard_folders_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _harness(tmp_path, monkeypatch)
    channel = scaffold("channel", "pager", root=tmp_path / "components")
    tool = scaffold("tool", "echo", root=tmp_path / "components")
    guard = scaffold("guard", "quiet", root=tmp_path / "components")
    for folder in (channel, tool, guard):
        ok, detail = check_folder(folder)
        assert ok, detail
    try:
        names = list_options("channel")
        assert "pager" in names
        assert "telegram" in names
        assert "terminal" not in names
    finally:
        from iris_ai.channels.registry import CHANNELS

        CHANNELS._entries.pop("pager", None)


def test_write_new_engine_template(tmp_path: Path) -> None:
    from iris_ai.cli.scaffold import write_new

    path = write_new("engine", "sketch", tmp_path / "out")
    text = path.read_text(encoding="utf-8")
    assert "class CustomEngine" in text
    assert "def streams" in text
    assert (path.parent / "component.toml").is_file()
