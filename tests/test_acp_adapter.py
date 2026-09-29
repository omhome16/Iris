"""The ACP adapter: what an editor gets, and the four decisions it makes.

Two layers here, on purpose:

- the **mapping** tests drive `IrisAcpAgent` against a recording stand-in for the
  client connection, so what is asserted is the adapter's own decisions (which
  notification, which kind, which verdict) rather than the SDK's framing;
- the **round-trip** test puts a real `ClientSideConnection` on the other end of
  an in-memory transport and lets the SDK frame everything, so the methods,
  parameter names and models are the protocol's, not ours.

No keys, no network, no database: the workspace is a `tmp_path`, memory is
SQLite, and the model is scripted. The `acp` extra is optional for the *harness*
and required for these tests, so the module skips (rather than fails) without it.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

acp = pytest.importorskip("acp", reason='the ACP adapter is an optional extra: pip install "iris-personal-ai[acp]"')

from acp import PROTOCOL_VERSION
from acp.contrib import default_permission_options
from acp.exceptions import RequestError
from acp.schema import (
    AgentMessageChunk,
    AllowedOutcome,
    DeniedOutcome,
    ImageContentBlock,
    RequestPermissionResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
)

from fakes import WizardLLM
from iris_ai.agent.tools import run_memory_search
from iris_ai.capabilities.models import MODELS
from iris_ai.config import settings
from iris_ai.engine import harness
from iris_ai.registry import Registration

DEAD_DSN = "postgresql+psycopg://iris:iris@127.0.0.1:1/iris_test"


class ScriptedLLM(WizardLLM):
    """A model with a script: one step per call, in order.

    A step is `{"text": ...}`, `{"tool": name, "args": {...}}`, or both — a model
    that narrates before acting is the normal case, and a script that can only do
    one or the other would let a mapping bug hide in the other path.

    `complete` is inherited from `WizardLLM` deliberately: the journal's
    reflection pass calls it on any turn that retrieved memory, and a double that
    overrides only the tool-calling methods makes a real provider call from a
    unit test.
    """

    def __init__(self, *steps: dict) -> None:
        super().__init__()
        self._steps = list(steps)
        self.tool_calls = 0

    def _step(self) -> dict:
        if self.tool_calls < len(self._steps):
            step = self._steps[self.tool_calls]
        else:
            step = {"text": "nothing left to say"}
        self.tool_calls += 1
        return step

    def _call(self, step: dict, index: int) -> dict:
        return {
            "id": f"call-{index}",
            "name": step["tool"],
            "args": step.get("args") or {},
            "type": "tool_call",
        }

    async def stream_complete_with_tools(self, messages, tools=None, **kwargs):
        index = self.tool_calls
        step = self._step()
        if step.get("text"):
            yield ("text", step["text"])
        if step.get("tool"):
            yield ("tool_call", {"id": f"call-{index}", "name": step["tool"], "args": step.get("args") or {}})
        yield ("done", "")

    async def complete_with_tools(self, messages, tools=None, **kwargs):
        index = self.tool_calls
        step = self._step()
        calls = [self._call(step, index)] if step.get("tool") else []
        return step.get("text", ""), calls, ""


@pytest.fixture
def acp_settings(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """The offline world the adapter tests run in: SQLite memory and threads."""
    monkeypatch.setattr(settings, "postgres_dsn", DEAD_DSN)
    monkeypatch.setattr(settings, "memory_backend", "sqlite")
    monkeypatch.setattr(settings, "checkpointer_backend", "auto")
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_dir", str(tmp_path / "sandbox"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "config" / "memory.db"))
    monkeypatch.setattr(settings, "checkpointer_path", str(tmp_path / "config" / "checkpoints.db"))
    monkeypatch.setattr(settings, "typesafe_api_key", "")
    return tmp_path


@pytest.fixture
def scripted(monkeypatch: pytest.MonkeyPatch):
    """Install a scripted model on the registry seam the boot path uses."""

    def _install(*steps: dict):
        llm = ScriptedLLM(*steps)
        entries = dict(MODELS._entries)
        entries["litellm"] = Registration(
            kind="model_backend",
            name="litellm",
            factory=lambda ledger=None, **kw: llm,
            source="test",
        )
        monkeypatch.setattr(MODELS, "_entries", entries)
        return llm

    return _install


class FakeConnection:
    """The slice of `AgentSideConnection` the adapter uses, recorded.

    Only `session_update` and `request_permission` exist here: a double that
    implemented more would be testing itself instead of the adapter.
    """

    def __init__(self, *, approve: bool = True) -> None:
        self.notes: list[tuple[str, object]] = []
        self.permissions: list[dict] = []
        self.approve = approve

    async def session_update(self, session_id: str, update: object) -> None:
        self.notes.append((session_id, update))

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.permissions.append({"session_id": session_id, "tool_call": tool_call, "options": list(options)})
        if self.approve:
            return RequestPermissionResponse(outcome=AllowedOutcome(outcome="selected", option_id="approve"))
        return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

    def text(self) -> str:
        """Everything the adapter sent as agent prose, in order."""
        return "".join(
            str(getattr(update, "content", "").text)
            for _sid, update in self.notes
            if isinstance(update, AgentMessageChunk) and getattr(update.content, "type", "") == "text"
        )

    def updates_of(self, kind: type) -> list[object]:
        return [update for _sid, update in self.notes if isinstance(update, kind)]


@contextlib.asynccontextmanager
async def _agent(scripted, *steps: dict):
    """A booted brain wired to the adapter, with the scripted model installed.

    Onboarding happens through the *files*, not through turns: these tests are
    about the turn mapping, and spending six model calls re-running the wizard
    through the graph would only make them slower and blur what failed.

    The `async with` is load-bearing, not style: `harness()` is an async context
    manager that owns the checkpointer's connection, so entering it without
    holding the manager tears the connection down as soon as the temporary is
    collected — and the first checkpoint write then fails on a closed database.
    """
    from iris_ai.interfaces.acp.agent import IrisAcpAgent
    from iris_ai.onboarding import OnboardingWizard

    scripted(*steps)
    async with harness(services=False) as brain:
        wizard = OnboardingWizard(brain.files, WizardLLM())
        for answer in ["Omar", "warm", "short", "UTC", "4"]:
            await wizard.apply_answer(answer)
        yield IrisAcpAgent(brain), brain


async def test_initialize_advertises_the_protocol_and_text_only(acp_settings, scripted):
    """The handshake states what we speak and what we accept, and the two agree
    with the code: text-only is advertised, and non-text prompts are refused."""
    async with _agent(scripted, {"text": "hi"}) as (agent, _brain):
        response = await agent.initialize(PROTOCOL_VERSION, None, None)
        assert response.protocol_version == PROTOCOL_VERSION
        assert response.agent_info is not None and response.agent_info.name == "iris"
        assert response.agent_info.version
        assert response.agent_capabilities is not None
        assert response.agent_capabilities.load_session is True
        caps = response.agent_capabilities.prompt_capabilities
        assert caps is not None
        assert not caps.image and not caps.audio and not caps.embedded_context


async def test_sessions_are_minted_listed_and_closed(acp_settings, scripted):
    """A session is the editor's handle on a thread; closing it forgets the
    handle, not the memory (a closed tab is not a reason to lose what was said)."""
    async with _agent(scripted, {"text": "hi"}) as (agent, _brain):
        first = await agent.new_session(cwd=str(acp_settings))
        second = await agent.new_session(cwd="/elsewhere")
        assert first.session_id != second.session_id

        listed = await agent.list_sessions()
        assert {s.session_id for s in listed.sessions} == {first.session_id, second.session_id}
        narrowed = await agent.list_sessions(cwd="/elsewhere")
        assert [s.session_id for s in narrowed.sessions] == [second.session_id]

        await agent.close_session(first.session_id)
        remaining = await agent.list_sessions()
        assert [s.session_id for s in remaining.sessions] == [second.session_id]


async def test_a_prompt_streams_the_reply_and_ends_the_turn(acp_settings, scripted):
    """The whole point: one ACP prompt drives one kernel turn."""
    async with _agent(scripted, {"text": "Two sugars, then."}) as (agent, _brain):
        conn = FakeConnection()
        agent.on_connect(conn)
        session = await agent.new_session(cwd=str(acp_settings))
        response = await agent.prompt(session.session_id, [TextContentBlock(type="text", text="how do I take it?")])
        assert response.stop_reason == "end_turn"
        assert conn.text() == "Two sugars, then."


async def test_a_tool_call_becomes_a_tool_call_the_editor_can_show(acp_settings, scripted):
    """Tool calls are reported with the id the kernel used, a kind for the icon,
    and a status that turns red only when the tool actually failed."""
    async with _agent(
        scripted,
        {"text": "Let me look.", "tool": "memory_search", "args": {"query": "tea"}},
        {"text": "Nothing in there."},
    ) as (agent, _brain):
        conn = FakeConnection()
        agent.on_connect(conn)
        session = await agent.new_session(cwd=str(acp_settings))
        await agent.prompt(session.session_id, [TextContentBlock(type="text", text="what files do you have?")])

        starts = conn.updates_of(ToolCallStart)
        assert [s.tool_call_id for s in starts] == ["call-0"]
        assert starts[0].kind == "search"
        assert starts[0].raw_input == {"query": "tea"}

        progress = conn.updates_of(ToolCallProgress)
        assert [p.tool_call_id for p in progress] == ["call-0"]
        assert progress[0].status == "completed"


async def test_an_approval_becomes_a_permission_request(acp_settings, scripted):
    """The interrupt is shown to the owner as a permission request, and the
    answer comes back through the kernel's own gate.

    `forget` is the tool used because it is the smallest one that interrupts: it
    searches memory, then asks before superseding. The memory is seeded and
    indexed the way capture would, so the search has something honest to find.
    """
    async with _agent(
        scripted,
        {"tool": "forget", "args": {"query": "jasmine"}},
        {"text": "Done — that one is retired."},
    ) as (agent, brain):
        # `forget` edits curated owner memory, and only MEMORY.md — a daily note
        # is append-only and matching one is a documented dead end, so the entry
        # has to be written where the tool actually goes.
        brain.files.write_curated(brain.files.memory, "# MEMORY.md\n\n- [8] Omar prefers jasmine tea\n")
        await brain.runtime.reindexer.reindex_all()
        assert await run_memory_search(brain.runtime, "jasmine")

        conn = FakeConnection(approve=True)
        agent.on_connect(conn)
        session = await agent.new_session(cwd=str(acp_settings))
        response = await agent.prompt(session.session_id, [TextContentBlock(type="text", text="forget the tea thing")])

        assert response.stop_reason == "end_turn"
        assert len(conn.permissions) == 1
        asked = conn.permissions[0]
        # The id is the kernel's call id, which is what makes the grant bindable.
        assert asked["tool_call"].tool_call_id == "call-0"
        assert [o.kind for o in asked["options"]] == [o.kind for o in default_permission_options()]
        assert "jasmine" in str(asked["tool_call"].content) or "forget" in str(asked["tool_call"].content)
        assert "retired" in conn.text()

        daily = brain.files.read(brain.files.memory)
        assert "superseded" in daily, "an approved forget must actually supersede the entry"


async def test_a_refused_approval_leaves_the_world_alone(acp_settings, scripted):
    """A dismissed dialog is a refusal, not an exception and not a silent yes."""
    async with _agent(
        scripted,
        {"tool": "forget", "args": {"query": "jasmine"}},
        {"text": "(the model should not get here)"},
    ) as (agent, brain):
        brain.files.write_curated(brain.files.memory, "# MEMORY.md\n\n- [8] Omar prefers jasmine tea\n")
        await brain.runtime.reindexer.reindex_all()
        before = brain.files.read(brain.files.memory)

        conn = FakeConnection(approve=False)
        agent.on_connect(conn)
        session = await agent.new_session(cwd=str(acp_settings))
        response = await agent.prompt(session.session_id, [TextContentBlock(type="text", text="forget it")])

        assert response.stop_reason == "end_turn"
        assert conn.permissions, "the owner was asked"
        assert brain.files.read(brain.files.memory) == before
        assert "jasmine" not in conn.text()


async def test_a_prompt_we_cannot_honor_is_an_error_not_a_silent_drop(acp_settings, scripted):
    """We advertise text-only, so an image is refused with the reason."""
    async with _agent(scripted, {"text": "hi"}) as (agent, _brain):
        agent.on_connect(FakeConnection())
        session = await agent.new_session(cwd=str(acp_settings))
        with pytest.raises(RequestError) as exc:
            await agent.prompt(
                session.session_id,
                [ImageContentBlock(type="image", data="AAAA", mime_type="image/png")],
            )
        assert exc.value.code == -32602
        assert "image" in str(exc.value.data)


def test_a_resource_link_is_named_in_the_message():
    """A referenced file the adapter cannot read is still *visible* to the model
    as a reference — the alternative is a turn that silently ignores context."""
    from acp.schema import ResourceContentBlock

    from iris_ai.interfaces.acp.agent import _text_of

    text = _text_of(
        [
            TextContentBlock(type="text", text="what is this?"),
            ResourceContentBlock(type="resource_link", name="notes.md", uri="file:///tmp/notes.md"),
        ]
    )
    assert text.splitlines()[0] == "what is this?"
    assert "notes.md" in text and "file:///tmp/notes.md" in text


async def test_a_prompt_for_an_unknown_session_is_refused(acp_settings, scripted):
    """Minting a thread the client cannot list would be a silent mismatch."""
    async with _agent(scripted, {"text": "hi"}) as (agent, _brain):
        agent.on_connect(FakeConnection())
        with pytest.raises(RequestError) as exc:
            await agent.prompt("nope", [TextContentBlock(type="text", text="hello?")])
        assert exc.value.code == -32602
        assert "unknown session" in str(exc.value.data)


async def test_cancel_stops_the_running_turn(acp_settings, scripted, monkeypatch):
    """`session/cancel` is a notification, so the turn is the thing cancelled —
    and the prompt answers with the protocol's own word for it.

    The fake announces itself on an `Event` rather than the test sleeping a
    guessed interval, so the cancel lands *during* the provider call every run
    instead of usually.
    """

    class Slow(ScriptedLLM):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()

        async def stream_complete_with_tools(self, messages, tools=None, **kwargs):
            self.started.set()
            await asyncio.sleep(30)
            yield ("text", "too late")

    slow = Slow()

    def install(*_steps: dict):
        entries = dict(MODELS._entries)
        entries["litellm"] = Registration(
            kind="model_backend", name="litellm", factory=lambda ledger=None, **kw: slow, source="test"
        )
        monkeypatch.setattr(MODELS, "_entries", entries)
        return slow

    async with _agent(install) as (agent, _brain):
        conn = FakeConnection()
        agent.on_connect(conn)
        session = await agent.new_session(cwd=str(acp_settings))
        task = asyncio.create_task(
            agent.prompt(session.session_id, [TextContentBlock(type="text", text="take your time")])
        )
        await asyncio.wait_for(slow.started.wait(), timeout=20)
        await agent.cancel(session.session_id)
        response = await asyncio.wait_for(task, timeout=20)
        assert response.stop_reason == "cancelled"
        assert "cancelled" in conn.text()


def test_tool_kind_is_conservative_about_names_it_does_not_know():
    """`other` is the honest answer for an unknown name — including an external
    server's tool, whose kind is that server's business, not ours."""
    from iris_ai.interfaces.acp.agent import tool_kind

    assert tool_kind("file_read") == "read"
    assert tool_kind("file_write") == "edit"
    assert tool_kind("forget") == "delete"
    assert tool_kind("skill_run") == "execute"
    assert tool_kind("some/server_tool") == "other"
    assert tool_kind("") == "other"


# ── the real thing: SDK framing on both ends ────────────────────────────────


async def test_a_prompt_survives_a_real_jsonrpc_round_trip(acp_settings, scripted):
    """A `ClientSideConnection` drives the adapter over an in-memory transport.

    This is the test that would catch a renamed method, a parameter passed
    positionally that the router passes by name, or a model built with the wrong
    field names — things the recording-double tests cannot see because they never
    serialize anything.
    """
    from acp import run_agent
    from acp._transport import memory_transport_pair
    from acp.client.connection import ClientSideConnection

    scripted({"text": "Framed correctly."})
    async with harness(services=False) as brain:
        from iris_ai.interfaces.acp.agent import IrisAcpAgent
        from iris_ai.onboarding import OnboardingWizard

        wizard = OnboardingWizard(brain.files, WizardLLM())
        for answer in ["Omar", "warm", "short", "UTC", "4"]:
            await wizard.apply_answer(answer)

        agent = IrisAcpAgent(brain)
        agent_to_client, client_to_agent = memory_transport_pair()

        class Client:
            def __init__(self) -> None:
                self.chunks: list[str] = []
                self.conn: object | None = None

            def on_connect(self, conn) -> None:
                self.conn = conn

            async def session_update(self, session_id, update) -> None:
                content = getattr(update, "content", None)
                if getattr(content, "type", "") == "text":
                    self.chunks.append(str(content.text))

            async def request_permission(self, session_id, tool_call, options, **kwargs):
                return RequestPermissionResponse(outcome=DeniedOutcome(outcome="cancelled"))

        client = Client()
        server = asyncio.create_task(run_agent(agent, agent_to_client))
        try:
            conn = ClientSideConnection(client, client_to_agent)
            hello = await conn.initialize(protocol_version=PROTOCOL_VERSION)
            assert hello.protocol_version == PROTOCOL_VERSION
            session = await conn.new_session(cwd=str(acp_settings), mcp_servers=[])
            result = await conn.prompt(
                session_id=session.session_id,
                prompt=[TextContentBlock(type="text", text="say something")],
            )
            assert result.stop_reason == "end_turn"
            assert "Framed correctly." in "".join(client.chunks)
        finally:
            with contextlib.suppress(Exception):
                await client_to_agent.close()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await asyncio.wait_for(server, timeout=5)
