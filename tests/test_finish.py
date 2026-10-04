"""The exits that were still open: jail, pipelines, engine, Discord, export, v0."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_ai.agent.chat import _engine_failed
from iris_ai.components import attach
from iris_ai.memory.dreaming import _fold_consolidator
from iris_ai.pipeline import component_names
from iris_ai.plug import component_digest, simulate_staged
from iris_ai.sdk.context import ComponentContext


class _Runtime:
    def __init__(self) -> None:
        self.context_builder = None
        self.capture_policy = None
        self.dreams = "built-in"


def test_explain_names_the_pipeline_stages():
    from iris_ai.explain import render_trace

    text = render_trace(
        {
            "model": "groq/test",
            "pipelines": {
                "context": [{"name": "default"}, {"name": "temporal-rag"}],
                "capture": [{"name": "default"}, {"name": "redact"}],
                "persona": [{"name": "file"}],
                "consolidator": [{"name": "dreaming"}],
            },
            "events": [
                {
                    "kind": "pipeline",
                    "pipeline": "context",
                    "stages": [{"name": "default"}, {"name": "budget"}],
                }
            ],
        }
    )
    assert "Pipelines" in text
    assert "default, budget" in text
    assert "redact" in text
    assert "dreaming" in text


def test_v0_context_is_refused():
    with pytest.raises(TypeError, match=r"removed in 0\.6"):
        ComponentContext(object())


def test_a_comma_list_is_a_pipeline():
    runtime = _Runtime()
    attach(runtime, {"components": {"context": "default,temporal-rag", "persona": "file,blank"}})
    assert runtime.pipelines["context"] == ["default", "temporal-rag"]
    assert runtime.persona_choice == "file,blank"
    assert component_names({"capture": {"pipeline": "default,redact"}}, "capture", default="default") == [
        "default",
        "redact",
    ]


def test_three_engine_failures_roll_back_to_react():
    runtime = SimpleNamespace(engine_fails=0, engine_name="plan-execute")
    graph = SimpleNamespace(runtime=runtime, engine_name="plan-execute")
    assert "failure 1" in _engine_failed(graph, RuntimeError("nope"))["engine_event"]
    _engine_failed(graph, RuntimeError("nope"))
    assert _engine_failed(graph, RuntimeError("nope"))["engine_event"] == "rolled back to react"
    assert runtime.engine_name == "react"


def test_staged_simulation_reads_the_world_and_leaves_the_digest(tmp_path: Path):
    folder = tmp_path / "component"
    folder.mkdir()
    (folder / "component.toml").write_text(
        'kind = "persona"\nname = "demo"\nentry = "component:Demo"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Demo:\n    def text(self) -> str:\n        return 'ok'\n",
        encoding="utf-8",
    )
    world = tmp_path / "world"
    world.mkdir()
    (world / "marker.txt").write_text("fixture\n", encoding="utf-8")
    before = component_digest(folder)
    with pytest.raises(PermissionError):
        simulate_staged(folder, world, live=True)
    ok, detail = simulate_staged(folder, world, live=False)
    assert ok, detail
    assert "world=fixture" in detail
    assert component_digest(folder) == before


def test_export_as_plugin_writes_plugin_json(tmp_path: Path):
    from iris_ai.cli.components_cmd import run

    dest = tmp_path / "plugin"
    code = run("export", "context", "temporal-rag", as_plugin=str(dest))
    assert code == 0
    assert (dest / "plugin.json").is_file()


def test_eval_engine_live_prints_the_paired_comparison(capsys):
    from iris_ai.cli.eval_cmd import run

    assert run("engine", live=True, archive=False) == 0
    captured = capsys.readouterr().out
    assert "plan-execute vs react" in captured
    assert "tool_calls=" in captured
    assert "latency_ms=" in captured
    assert "scripted tools" in captured


@pytest.mark.asyncio
async def test_consolidator_stages_fold_into_the_plan():
    engine = SimpleNamespace(pipeline=["dreaming", "conflict-resolver"])
    plan = SimpleNamespace(conflicts=())
    await _fold_consolidator(engine, plan)
    assert plan.conflicts == ()


def _discord():
    path = Path(__file__).resolve().parents[1] / "examples" / "iris-discord" / "iris_discord" / "channel.py"
    spec = importlib.util.spec_from_file_location("discord_channel_finish", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_discord_receives_a_dm_and_posts_approval_buttons():
    module = _discord()

    class _Typing:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

    class _Room:
        def __init__(self) -> None:
            self.sent: list[tuple] = []

        async def send(self, text, view=None):
            self.sent.append((text, view))
            return SimpleNamespace(id="9")

        def typing(self):
            return _Typing()

    room = _Room()
    started: dict[str, str] = {}

    async def start(token: str) -> None:
        started["token"] = token
        await asyncio.sleep(3600)

    async def close() -> None:
        return None

    async def edit_message(message_id: str, text: str) -> None:
        room.sent.append(("edit", message_id, text))

    client = SimpleNamespace(
        user=SimpleNamespace(id="1"),
        start=start,
        close=close,
        edit_message=edit_message,
        get_channel=lambda _conversation: room,
    )
    channel = module.DiscordChannel(token="secret", client=client)
    await channel.connect()
    await asyncio.sleep(0)
    assert started["token"] == "secret"
    channel.push(
        SimpleNamespace(
            author=SimpleNamespace(id="2", display_name="Ada"),
            content="hello",
            guild=None,
            mentions=[],
            channel=SimpleNamespace(id="7"),
            thread=None,
            id="m1",
        )
    )
    item = await anext(channel.receive())
    assert item.text == "hello"
    assert item.conversation == "dm:7"
    assert await channel.send(SimpleNamespace(text="hi", conversation="dm:7")) == "9"
    assert await channel.ask_approval("approve?") == "pending"
    assert room.sent[-1][1]["components"][0]["custom_id"] == "iris-approve"
    await channel.edit("9", "done")
    await channel.typing("dm:7")
    await channel.close()
    if channel._runner is not None:
        with pytest.raises(asyncio.CancelledError):
            await channel._runner
