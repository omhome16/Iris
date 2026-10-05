"""The subprocess host cannot see the kernel, and grants only shrink."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from iris_ai.isolation.broker import Authority, CapabilityBroker
from iris_ai.isolation.host import open_component
from iris_ai.isolation.rpc import read_message, write_message
from iris_ai.sdk.context import WorkspaceAccess


def _folder(path: Path, body: str, permissions: list[str] | None = None) -> Path:
    path.mkdir(parents=True)
    perms = ""
    if permissions:
        joined = ", ".join(f'"{item}"' for item in permissions)
        perms = f"permissions = [{joined}]\n"
    (path / "component.toml").write_text(
        'kind = "context"\nname = "hosted"\napi_version = "iris/v1"\n'
        'entry = "component:Component"\n' + perms,
        encoding="utf-8",
    )
    (path / "component.py").write_text(body, encoding="utf-8")
    return path


def test_frames_round_trip_and_reject_a_huge_message():
    stream = io.BytesIO()
    write_message(stream, {"ok": True})
    stream.seek(0)
    assert read_message(stream) == {"ok": True}
    huge = io.BytesIO()
    with pytest.raises(ValueError, match="1"):
        write_message(huge, {"blob": "x" * (1024 * 1024 + 10)})


def test_authority_can_only_narrow():
    parent = Authority(["files.read", "memory.search"], llm_cap=2)
    child = parent.narrow(["files.read"], llm_cap=1)
    assert child.grants == frozenset({"files.read"})
    assert child.llm_cap == 1
    with pytest.raises(PermissionError, match="cannot add"):
        parent.narrow(["files.read", "llm.complete"])
    with pytest.raises(PermissionError, match="cap"):
        parent.narrow(llm_cap=4)
    with pytest.raises(ValueError, match="unknown"):
        Authority(["net.request"])


def test_broker_refuses_a_missing_grant_and_a_path_escape(tmp_path: Path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.txt").write_text("ok", encoding="utf-8")
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    access = WorkspaceAccess(tmp_path)
    broker = CapabilityBroker(
        Authority(["files.read"]),
        {"files.read": lambda params: access.read(str(params["path"]))},
    )
    assert broker.dispatch("files.read", {"path": "notes/a.txt"}) == "ok"
    with pytest.raises(PermissionError):
        broker.dispatch("memory.search", {"query": "x"})
    with pytest.raises(PermissionError):
        broker.dispatch("files.read", {"path": ".env"})
    with pytest.raises(PermissionError):
        broker.dispatch("files.read", {"path": "../.env"})


def test_hosted_component_cannot_import_the_kernel_or_see_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("IRIS_SECRET_SENTINEL", "nope")
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    folder = _folder(
        tmp_path / "component",
        "import os\n"
        "from iris_ai.sdk.types import ContextBlock, ContextResult\n"
        "class Component:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "    async def assemble(self, request):\n"
        "        secret = 'yes' if 'IRIS_SECRET_SENTINEL' in os.environ else 'no'\n"
        "        try:\n"
        "            import iris_ai.config\n"
        "            kernel = 'yes'\n"
        "        except ModuleNotFoundError:\n"
        "            kernel = 'no'\n"
        "        try:\n"
        "            open(self.ctx.options['probe'], encoding='utf-8').read()\n"
        "            leaked = 'yes'\n"
        "        except Exception:\n"
        "            leaked = 'no'\n"
        "        text = f'secret={secret} kernel={kernel} leaked={leaked} msg={request.message}'\n"
        "        return ContextResult(blocks=(ContextBlock(title='', text=text),))\n",
    )
    hosted = open_component(folder, kind="context", name="hosted", options={"probe": str(outside)})
    try:
        result = asyncio.run(hosted.assemble(SimpleNamespace(message="hi", session_id="s", origin="owner")))
    finally:
        hosted.close()
    text = result.render()
    assert "secret=no" in text
    assert "kernel=no" in text
    assert "leaked=no" in text
    assert "msg=hi" in text


def test_a_call_without_a_grant_is_refused(tmp_path: Path):
    folder = _folder(
        tmp_path / "component",
        "from iris_ai.sdk.types import ContextBlock, ContextResult\n"
        "class Component:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "    async def assemble(self, request):\n"
        "        await self.ctx.memory.search('q')\n"
        "        return ContextResult(blocks=(ContextBlock(title='', text='nope'),))\n",
    )
    hosted = open_component(folder, kind="context", name="hosted")
    try:
        with pytest.raises(RuntimeError, match="not granted"):
            asyncio.run(hosted.assemble(SimpleNamespace(message="hi", session_id="s", origin="owner")))
    finally:
        hosted.close()


def test_a_granted_memory_search_returns_the_parent_rows(tmp_path: Path):
    folder = _folder(
        tmp_path / "component",
        "from iris_ai.sdk.types import ContextBlock, ContextResult\n"
        "class Component:\n"
        "    def __init__(self, ctx, **options):\n"
        "        self.ctx = ctx\n"
        "    async def assemble(self, request):\n"
        "        rows = await self.ctx.memory.search(request.message)\n"
        "        return ContextResult(blocks=(ContextBlock(title='', text=rows[0]['content']),))\n",
        permissions=["memory.search"],
    )
    hosted = open_component(
        folder,
        kind="context",
        name="hosted",
        handlers={"memory.search": lambda params: [{"content": "hit:" + params["query"]}]},
    )
    try:
        result = asyncio.run(hosted.assemble(SimpleNamespace(message="plans", session_id="s", origin="owner")))
    finally:
        hosted.close()
    assert result.render() == "hit:plans"
