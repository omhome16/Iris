"""0.7 foundations: one lock, a stable digest, and eval that runs the candidate."""

from __future__ import annotations

import json
from pathlib import Path

from iris_ai.agent.chat import _tool_failed
from iris_ai.components.lock import pin, read_lock
from iris_ai.eval.score import persona_text
from iris_ai.plug import component_digest, construct, load_class


def test_v1_and_v2_locks_collapse_to_one_schema(tmp_path: Path):
    path = tmp_path / "components.lock"
    path.write_text(
        json.dumps(
            {
                "version": 2,
                "kinds": {"context": {"active": "recall-first", "digest": "aaa", "previous": "default"}},
                "persona": {"active": "haiku", "previous": "file", "fails": 2},
            }
        ),
        encoding="utf-8",
    )
    data = read_lock(path)
    assert data["version"] == 3
    assert data["kinds"]["context"]["digest"] == "aaa"
    assert data["kinds"]["persona"]["active"] == "haiku"
    pin("persona", "haiku", source="local", digest="bbb", path=path)
    assert read_lock(path)["kinds"]["persona"]["digest"] == "bbb"
    assert read_lock(path)["kinds"]["context"]["digest"] == "aaa"


def test_digest_ignores_pycache(tmp_path: Path):
    folder = tmp_path / "persona" / "terse"
    folder.mkdir(parents=True)
    (folder / "component.py").write_text("class Component:\n    pass\n", encoding="utf-8")
    (folder / "component.toml").write_text('kind = "persona"\nname = "terse"\n', encoding="utf-8")
    before = component_digest(folder)
    cache = folder / "__pycache__"
    cache.mkdir()
    (cache / "component.cpython-312.pyc").write_bytes(b"\x00not-source")
    assert component_digest(folder) == before


def test_a_local_class_is_not_handed_the_runtime():
    class Evil:
        def __init__(self, runtime):
            self.runtime = runtime

    try:
        construct(Evil, object())
    except TypeError as exc:
        assert "ctx" in str(exc)
    else:
        raise AssertionError("construct handed the runtime to a local class")


def test_persona_eval_uses_the_folder_not_the_name(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "harness.toml").write_text("[components]\n", encoding="utf-8")
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "harness_config", str(tmp_path / "config" / "harness.toml"))
    folder = tmp_path / "components" / "persona" / "strict-reviewer"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "persona"\nname = "strict-reviewer"\napi_version = "iris/v1"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Component:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "    def text(self) -> str:\n"
        "        return 'Be helpful.'\n",
        encoding="utf-8",
    )
    assert "Verdict:" not in persona_text("strict-reviewer")
    assert load_class(folder).component_name == "strict-reviewer"


def test_an_owner_denial_is_not_a_tool_failure():
    assert _tool_failed('{"ok": false, "error": "activation cancelled", "refused": "owner_denied"}') is False
    assert _tool_failed('{"ok": false, "error": "disk full"}') is True
