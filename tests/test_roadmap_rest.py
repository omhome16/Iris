"""The rest of the open-harness plan: control, probation, catalog, kinds, wheels, bench, propose."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
from pathlib import Path

from iris_ai.components.lock import pin, probation_left, read_lock, tick_probation
from iris_ai.config import settings
from iris_ai.engines.dispatch import resolve
from iris_ai.lifecycle.memory_swap import swap_plan
from iris_ai.plug import note_failure
from iris_ai.buildlock import inspect


def _harness(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "config"
    config.mkdir()
    harness = config / "harness.toml"
    harness.write_text("[components]\n", encoding="utf-8")
    monkeypatch.setattr(settings, "harness_config", str(harness))
    return harness


def test_one_failure_during_probation_rolls_back(tmp_path: Path, monkeypatch) -> None:
    _harness(tmp_path, monkeypatch)
    pin("context", "custom", source="local", digest="a" * 64)
    assert probation_left("context") == 3
    message = note_failure("context", "custom")
    assert message is not None
    assert "rolled back" in message
    assert read_lock()["kinds"]["context"]["active"] != "custom"


def test_probation_ends_after_three_successful_calls(tmp_path: Path, monkeypatch) -> None:
    _harness(tmp_path, monkeypatch)
    pin("context", "custom", source="local", digest="a" * 64)
    for _ in range(3):
        tick_probation("context")
    assert probation_left("context") == 0
    assert note_failure("context", "custom") is None
    assert read_lock()["kinds"]["context"]["fails"] == 1


def test_plan_refuses_a_git_dependency_and_an_unknown_grant(tmp_path: Path) -> None:
    folder = tmp_path / "widget"
    folder.mkdir()
    (folder / "component.toml").write_text(
        'kind = "context"\nname = "widget"\npermissions = ["memory.search", "net.request"]\n'
        "[dependencies.leftpad]\nversion = \"git+https://example.com/leftpad\"\nsha256 = \""
        + ("ab" * 32)
        + "\"\n",
        encoding="utf-8",
    )
    from iris_ai.planning.catalog import plan

    report = plan(folder)
    assert "net.request" in report["refused_grants"]
    assert "memory.search" in report["grants"]
    assert report["build"] == "refused"
    assert any("pinned wheel" in item for item in report["build_problems"])


def test_a_missing_wheel_is_pending_and_a_matching_wheel_is_built(tmp_path: Path) -> None:
    folder = tmp_path / "boxed"
    wheels = folder / "wheels"
    wheels.mkdir(parents=True)
    blob = b"wheel-bytes"
    digest = hashlib.sha256(blob).hexdigest()
    (folder / "component.toml").write_text(
        'kind = "tool"\nname = "boxed"\n'
        "[dependencies.boxed]\nversion = \"1.0.0\"\n"
        f'sha256 = "{digest}"\n',
        encoding="utf-8",
    )
    pending = inspect(folder)
    assert pending["status"] == "build_pending"
    (wheels / "boxed-1.0.0-py3-none-any.whl").write_bytes(blob)
    built = inspect(folder)
    assert built["status"] == "built"
    assert (folder / "deps.lock").is_file()


def test_a_declarative_command_expands_and_a_builtin_does_not(tmp_path: Path, monkeypatch) -> None:
    _harness(tmp_path, monkeypatch)
    prompt = tmp_path / "components" / "command" / "ship" / "prompt.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("Ship this: $args", encoding="utf-8")
    from iris_ai.commands.kinds import expand

    assert expand("/ship the notes") == "Ship this: the notes"
    assert expand("/help") is None


def test_engine_dispatch_keeps_react_and_plan_apart() -> None:
    react = resolve("react")
    plan = resolve("plan-execute")
    assert react.streams() is True
    assert react.wants_plan({}) is False
    assert plan.streams() is False
    state = {"engine_phase": "act"}
    plan.after_tools(state)
    assert state["engine_phase"] == "verify"
    assert plan.short_circuit({"engine_phase": "verify"}) == "verify"


def test_select_pins_a_local_folder(tmp_path: Path, monkeypatch) -> None:
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "context" / "demo"
    folder.mkdir(parents=True)
    (folder / "component.py").write_text("class Component:\n    pass\n", encoding="utf-8")
    (folder / "component.toml").write_text(
        'kind = "context"\nname = "demo"\napi_version = "iris/v1"\n',
        encoding="utf-8",
    )
    from iris_ai.lifecycle.control import select

    select("context", "demo")
    row = read_lock()["kinds"]["context"]
    assert row["active"] == "demo"
    assert row["digest"]
    assert row["probation_remaining"] == 3


def test_memory_swap_is_a_rebuild_plan(tmp_path: Path) -> None:
    bare = swap_plan("sqlite", "pgvector", root=tmp_path)
    assert bare["applied"] is False
    assert bare["problems"]
    (tmp_path / "MEMORY.md").write_text("# memory\n", encoding="utf-8")
    ready = swap_plan("sqlite", "pgvector", root=tmp_path)
    assert ready["problems"] == []
    assert ready["command"] == "iris migrate --to pgvector"
    assert ready["applied"] is False


def test_a_tool_component_runs_in_the_host_after_approval(tmp_path: Path, monkeypatch) -> None:
    _harness(tmp_path, monkeypatch)
    folder = tmp_path / "components" / "tool" / "echo"
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "tool"\nname = "echo"\nentry = "component:Component"\n'
        'description = "echo"\npermissions = []\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Component:\n"
        "    def __init__(self, ctx):\n"
        "        self.ctx = ctx\n"
        "    def run(self, request):\n"
        "        return request.message\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("iris_ai.agent.tools._honour_approval", lambda shown: "approved")
    from iris_ai.kinds.tools import load_kind_tools
    from iris_ai.toolpolicy import EXTERNAL_TOOLS

    tools = load_kind_tools()
    try:
        echo = next(tool for tool in tools if tool.name == "echo")
        body = json.loads(asyncio.run(echo.handler(message="hi")))
        assert body["text"] == "hi"
    finally:
        EXTERNAL_TOOLS.pop("echo", None)


def test_propose_writes_a_worktree_and_does_not_apply(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "commit", "-m", "init"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    from iris_ai.core.propose import propose

    result = propose("change the clock", repo=repo, dest=tmp_path / "proposal")
    assert result["applied"] is False
    assert (tmp_path / "proposal" / "PROPOSAL.md").is_file()
    assert (repo / "README.md").read_text(encoding="utf-8") == "hi\n"
    assert not (repo / "src" / "iris_ai" / "approval.py").exists()


def test_scripted_bench_passes() -> None:
    from iris_ai.bench.harness import scripted

    report = scripted()
    assert report["ok"] is True
    assert report["hostile_blocked"] is True
