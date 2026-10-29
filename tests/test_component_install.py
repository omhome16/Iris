"""`iris components add` checks, stores, and pins. It does not clone a URL."""

from __future__ import annotations

from pathlib import Path

import pytest

from iris_ai.artifacts.store import verify
from iris_ai.components.lock import read_lock
from iris_ai.lifecycle.journal import read_events


def _harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    harness = tmp_path / "config" / "harness.toml"
    harness.parent.mkdir()
    harness.write_text("[components]\n", encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(harness))
    monkeypatch.chdir(tmp_path)


def _persona(folder: Path, body: str = "return 'hi'\n") -> None:
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "persona"\nname = "voice"\napi_version = "iris/v1"\nentry = "component:Voice"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Voice:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        f"    def text(self):\n        {body}",
        encoding="utf-8",
    )


def test_a_git_url_is_refused_even_with_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _harness(tmp_path, monkeypatch)

    def boom(*_args, **_kwargs):
        raise AssertionError("a remote install must not spawn git")

    monkeypatch.setattr("subprocess.run", boom)
    from iris_ai.cli.components_cmd import run

    code = run("add", "https://example.invalid/evil.git", yes=True)
    assert code == 2
    assert read_lock().get("kinds") == {}


def test_add_checks_then_pins_the_stored_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _harness(tmp_path, monkeypatch)
    source = tmp_path / "incoming" / "voice"
    _persona(source)
    from iris_ai.cli.components_cmd import run

    code = run("add", str(source), yes=True)
    assert code == 0
    row = read_lock()["kinds"]["persona"]
    assert row["active"] == "voice"
    assert row["digest"]
    assert verify(row["digest"])
    events = read_events()
    assert events[-1]["event"] == "select"
    assert events[-1]["digest"] == row["digest"]
    installed = tmp_path / "components" / "persona" / "voice" / "component.py"
    assert installed.is_file()


def test_lock_update_stores_the_bytes_it_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "persona" / "voice"
    _persona(folder)
    from iris_ai.components.lock import pin

    pin("persona", "voice", source="local", digest="0" * 64)
    from iris_ai.cli.components_cmd import run

    assert run("lock", "update") == 0
    digest = read_lock()["kinds"]["persona"]["digest"]
    assert digest != "0" * 64
    assert verify(digest)
    assert read_events()[-1]["event"] == "select"
    assert read_events()[-1]["digest"] == digest


def test_rollback_records_a_lifecycle_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _harness(tmp_path, monkeypatch)
    from iris_ai.components.lock import pin

    pin("persona", "voice", source="local", digest="abc")
    from iris_ai.cli.components_cmd import run

    assert run("rollback", "persona") == 0
    assert read_events()[-1]["event"] == "rollback"


def test_add_does_not_copy_a_folder_that_fails_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    _harness(tmp_path, monkeypatch)
    source = tmp_path / "incoming" / "voice"
    _persona(source, body="raise RuntimeError('broken')\n")
    from iris_ai.cli.components_cmd import run

    code = run("add", str(source), yes=True)
    assert code == 1
    assert not (tmp_path / "components" / "persona" / "voice").exists()
    assert read_lock().get("kinds") == {}
