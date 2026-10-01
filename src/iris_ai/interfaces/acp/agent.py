"""`IrisAcpAgent` — the ACP adapter, and the mapping decisions it makes.

This is a **client of the kernel**, exactly like `iris_ai.cli.chat`: every turn
goes through `Harness.stream` / `Harness.resume`, so memory, judgments,
approvals, budgets and traces behave identically whether the owner is in a
terminal, an HTTP client, or an editor. The adapter owns no brain, no store and
no policy of its own — where it *does* decide something, that decision is
written down here and in `DOCS.md`, because an adapter that quietly answers
a protocol question differently from the CLI is a second behavior to debug.

The four decisions worth reading before the code:

**One harness, many sessions.** ACP sessions map to threads by
`THREAD_PREFIX + session_id`, so `session/load` continues the same memory after
an editor restart without a second registry. `session/new` mints the id.

**The editor's `cwd` does not widen the filesystem.** A client asking for a
directory is not the same as an owner granting it, so the session records the
cwd (it is reported back and logged) while tools keep running against the
harness workspace and its sandbox. Widening scope per session is a policy
feature, and `DOCS.md` says so.

**An approval is a permission request.** A kernel interrupt becomes
`session/request_permission`; the answer comes back through the same
`ApprovalGate` the CLI's `y/n` uses, so a grant is still single-use and still
bound to the call id. The adapter never decides on the owner's behalf: a client
that dismisses the dialog is a refusal.

**Unsupported prompt content is an error, not a silent drop.** We advertise
text-only (`PromptCapabilities()`), so an image or audio block is refused with
`invalid_params`. Dropping it would answer a question the owner did not ask.

Deliberately absent: `session/set_mode` (Iris has no modes — the router answers
`method not found`), and streaming of the *resumed* half of an interrupted turn
(the kernel's resume path returns a final reply; the tool calls it makes are
journalled, not streamed).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from iris_ai import __version__

try:  # the optional extra — say so plainly if it is missing
    from acp import (
        PROTOCOL_VERSION,
        InitializeResponse,
        LoadSessionResponse,
        NewSessionResponse,
        PromptResponse,
    )
    from acp.contrib import default_permission_options
    from acp.exceptions import RequestError
    from acp.helpers import start_tool_call, text_block, tool_content, update_agent_message_text, update_tool_call
    from acp.interfaces import Agent as _AcpAgent

    # The SDK keeps its generated models in `acp.schema`; only the handful of
    # request/response pairs an adapter is expected to name are re-exported on
    # the package itself. Importing from `schema` is the supported path.
    from acp.schema import (
        AgentCapabilities,
        AllowedOutcome,
        Implementation,
        ListSessionsResponse,
        PromptCapabilities,
        SessionInfo,
        ToolCallUpdate,
    )
except ModuleNotFoundError as exc:  # pragma: no cover - the docs test covers the message
    raise ModuleNotFoundError(
        "the ACP adapter needs an optional extra: pip install \"iris-personal-ai[acp]\""
    ) from exc

from iris_ai.agent.chat import ApprovalRequired
from iris_ai.engine import Harness

log = logging.getLogger("iris.acp")

#: Namespacing the thread by the session id keeps ACP memory separate from the
#: CLI's `cli` thread and the API's `default` — a client must not be able to
#: collide with a thread it never opened.
THREAD_PREFIX = "acp:"

#: How many times one prompt may pause for approval. Each pause is a *distinct*
#: tool call (an approval is single-use), so a legitimate turn can ask a few
#: times; a client that answers "approve" to an endless stream of them is
#: stopped here rather than looping forever.
MAX_APPROVALS_PER_PROMPT = 8

#: Tool name -> ACP `ToolKind`, for the icon and grouping an editor shows. The
#: mapping is cosmetic and deliberately conservative: anything unrecognized is
#: `other` rather than a guess, and namespaced MCP tools (`server/tool`) are
#: `other` because their kind is the server's business, not ours.
_TOOL_KINDS: dict[str, str] = {
    "memory_search": "search",
    "deep_dive": "search",
    "file_list": "search",
    "find_tools": "search",
    "web_search": "search",
    "file_read": "read",
    "get_chat_history": "read",
    "inspect_mind": "read",
    "skill_list": "read",
    "file_create": "edit",
    "file_write": "edit",
    "remember": "edit",
    "note": "edit",
    "skill_write": "edit",
    "skill_revise": "edit",
    "skill_apply": "edit",
    "forget": "delete",
    "ingest_url": "fetch",
    "skill_run": "execute",
    "computer": "execute",
    "dream_now": "execute",
    "schedule_task": "think",
    "verify_answer": "think",
}


def tool_kind(name: str) -> str:
    """The ACP `kind` for a tool name (`other` when we do not know it)."""
    return _TOOL_KINDS.get(name, "other")


def _text_of(prompt: list[Any]) -> str:
    """Flatten ACP prompt blocks into one user message.

    Text is joined; a `resource_link` is rendered as a line naming its URI, so
    the model can see that context was referenced even though the adapter does
    not read the file itself. Anything else is refused loudly: we advertise
    text-only, and answering with an image silently dropped is worse than an
    error the client can show.
    """
    parts: list[str] = []
    for block in prompt:
        kind = getattr(block, "type", None)
        if kind == "text":
            parts.append(str(getattr(block, "text", "")))
        elif kind == "resource_link":
            uri = getattr(block, "uri", "")
            name = getattr(block, "name", "") or uri
            parts.append(f"[referenced: {name} ({uri})]")
        else:
            raise RequestError.invalid_params(
                {
                    "type": kind,
                    "reason": "this agent advertises text-only prompts "
                    "(agentCapabilities.promptCapabilities)",
                }
            )
    return "\n".join(p for p in parts if p).strip()


def _field(message: Any, key: str, default: Any = None) -> Any:
    """One field of a message in a state update.

    A `stream_mode="updates"` payload carries plain dicts for what the graph
    produced in-process, but message *objects* once a node returned `BaseMessage`
    instances — so both shapes are read here rather than at each use.
    """
    if isinstance(message, dict):
        return message.get(key, default)
    return getattr(message, key, default)


def _final_text(update: dict) -> str:
    """The reply text in one graph state update, if that update finished the turn."""
    for message in (update or {}).get("messages", []):
        if _field(message, "type") == "ai" and not _field(message, "tool_calls"):
            content = _field(message, "content")
            return str(content) if isinstance(content, str) else ""
    return ""


def _nodes(payload: dict) -> list[dict]:
    """Only the real node updates in an `updates` payload.

    A paused approval is stored on the thread under `__interrupt__`. That key is
    not a state update. Filtering once, here, is what keeps every access below
    from needing its own type guard.
    """
    return [update for update in (payload or {}).values() if isinstance(update, dict)]


def _tool_events(update: dict) -> list[tuple[str, dict]]:
    """`(id, call)` for each tool the model asked for, and `(id, result)` for each
    result, in the order the state update holds them."""
    events: list[tuple[str, dict]] = []
    for message in (update or {}).get("messages", []):
        if _field(message, "type") == "ai":
            for call in _field(message, "tool_calls") or []:
                events.append(("call", call if isinstance(call, dict) else dict(call)))
        elif _field(message, "type") == "tool":
            events.append(
                (
                    "result",
                    {
                        "tool_call_id": _field(message, "tool_call_id") or "",
                        "name": _field(message, "name") or "",
                        "content": _field(message, "content"),
                    },
                )
            )
    return events


def _failed(content: Any) -> bool:
    """Whether a tool result is a failure, per the surface's own contract
    (`{"ok": false, "error": ...}`), so the editor shows a red call only when the
    tool actually failed."""
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped.startswith("{"):
        return False
    import json

    try:
        parsed = json.loads(stripped)
    except ValueError:
        return False
    return isinstance(parsed, dict) and parsed.get("ok") is False


@dataclass
class _Session:
    """One editor conversation: the ACP id, the thread it continues, and the
    turn currently running (so `session/cancel` has something to cancel)."""

    session_id: str
    cwd: str
    additional_directories: tuple[str, ...] = ()
    task: asyncio.Task[Any] | None = None
    title: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def thread(self) -> str:
        return f"{THREAD_PREFIX}{self.session_id}"


class IrisAcpAgent(_AcpAgent):
    """The ACP `Agent` implementation: a mapping from ACP messages onto turns.

    Constructed with a booted `Harness` (the caller owns its lifetime), so the
    same object can hold the API's brain in a test or the stdio process's brain
    in an editor.
    """

    def __init__(self, brain: Harness) -> None:
        self.brain = brain
        self._conn: Any = None
        self._sessions: dict[str, _Session] = {}
        self._lock = asyncio.Lock()

    # ── connection ───────────────────────────────────────────────────────
    def on_connect(self, conn: Any) -> None:
        """The `AgentSideConnection`, needed to push `session/update`s back."""
        self._conn = conn

    # ── lifecycle ────────────────────────────────────────────────────────
    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: Any = None,
        client_info: Any = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        """Handshake. We answer with the protocol version *we* speak (1); a client
        that cannot speak it disconnects, which is the protocol's own rule."""
        client = getattr(client_info, "name", None) or "an ACP client"
        log.info("acp initialize from %s (protocol %s)", client, protocol_version)
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_capabilities=AgentCapabilities(
                load_session=True,
                # Text only, advertised rather than assumed: `_text_of` refuses
                # anything else, and the two must agree or the client is misled.
                prompt_capabilities=PromptCapabilities(),
            ),
            agent_info=Implementation(name="iris", title="Iris", version=__version__),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        """Open a session and mint its thread id.

        `mcp_servers` is where the client offers servers for this session. They
        are *reported* and not adopted: a server the client brought is untrusted
        by definition (nobody in this process vouched for it), and `DOCS.md`
        records that v1 answers with the configured servers only rather than
        opening a connection on a client's say-so.
        """
        session = _Session(
            session_id=uuid.uuid4().hex,
            cwd=cwd,
            additional_directories=tuple(additional_directories or ()),
        )
        async with self._lock:
            self._sessions[session.session_id] = session
        if mcp_servers:
            log.info(
                "acp session %s offered %d client MCP server(s); the configured "
                "servers are used (see DOCS.md — a client's server is untrusted)",
                session.session_id,
                len(mcp_servers),
            )
        log.info("acp session %s opened (cwd=%s, thread=%s)", session.session_id, cwd, session.thread)
        return NewSessionResponse(session_id=session.session_id)

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse:
        """Re-attach to a session id the client already has.

        Memory continuity is the point: the id maps back to the same thread, so
        the conversation the owner returns to is the one the kernel remembers.
        """
        session = _Session(
            session_id=session_id,
            cwd=cwd,
            additional_directories=tuple(additional_directories or ()),
        )
        async with self._lock:
            self._sessions[session_id] = session
        log.info("acp session %s loaded (thread=%s)", session_id, session.thread)
        return LoadSessionResponse()

    async def list_sessions(self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any) -> Any:
        """Sessions this process knows about, optionally narrowed to one cwd."""
        async with self._lock:
            sessions = list(self._sessions.values())
        if cwd is not None:
            sessions = [s for s in sessions if s.cwd == cwd]
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    session_id=s.session_id,
                    cwd=s.cwd,
                    title=s.title or None,
                    updated_at=s.updated_at,
                )
                for s in sessions
            ]
        )

    async def authenticate(self, method_id: str, **kwargs: Any) -> Any:
        """No-op: Iris runs on the owner's machine and authenticates the owner by
        being theirs. We advertise no auth methods, so a client should not ask."""
        return None

    async def close_session(self, session_id: str, **kwargs: Any) -> Any:
        """Forget the session. The thread is *not* deleted — memory is the
        owner's, and an editor closing a tab is not a reason to lose it."""
        async with self._lock:
            session = self._sessions.pop(session_id, None)
        if session is not None and session.task is not None and not session.task.done():
            session.task.cancel()
        return None

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        """`session/cancel` is a notification: stop the running turn.

        Cancelling the task propagates `CancelledError` into the graph, which is
        the only place that can stop an in-flight provider call. The prompt
        handler catches it and answers `stopReason: cancelled` — the protocol's
        own word for it.
        """
        session = self._sessions.get(session_id)
        if session is None or session.task is None or session.task.done():
            log.info("acp cancel for session %s with no turn running", session_id)
            return
        log.info("acp cancel for session %s", session_id)
        session.task.cancel()

    # ── the turn ─────────────────────────────────────────────────────────
    async def prompt(self, session_id: str, prompt: list[Any], **kwargs: Any) -> PromptResponse:
        """One turn, streamed back as `session/update` notifications."""
        session = self._sessions.get(session_id)
        if session is None:
            # A prompt for a session we never opened is a client bug, and saying
            # so is more useful than silently minting a thread it cannot list.
            raise RequestError.invalid_params({"sessionId": session_id, "reason": "unknown session"})
        text = _text_of(prompt)
        if not text:
            raise RequestError.invalid_params({"reason": "the prompt carried no usable text"})

        session.task = asyncio.current_task()
        session.updated_at = datetime.now(UTC).isoformat()
        try:
            return await self._run_turn(session, text)
        except asyncio.CancelledError:
            log.info("acp turn on session %s cancelled", session_id)
            await self._notify(session, "The turn was cancelled.")
            return PromptResponse(stop_reason="cancelled")
        finally:
            session.task = None

    # ── the turn ─────────────────────────────────────────────────────────
    async def _run_turn(self, session: _Session, text: str) -> PromptResponse:
        """Stream the turn, then settle any approvals it raised.

        The interrupt arrives as the *last* thing the stream yields (the graph
        has already paused by then), so the drain only records it; the loop
        below does the asking. Keeping the two apart is what makes a second
        approval — a second action in one turn — settle in the same turn instead
        of being dropped.
        """
        streamed, failed, pending, refused = await self._drain(session, text)
        if refused:
            return PromptResponse(stop_reason="refusal")

        # Approvals, in the same turn, until none is waiting.
        asked = 0
        while pending is not None:
            asked += 1
            if asked > MAX_APPROVALS_PER_PROMPT:
                log.warning(
                    "acp session %s hit the approval ceiling (%d); stopping the turn",
                    session.session_id,
                    MAX_APPROVALS_PER_PROMPT,
                )
                await self._notify(
                    session,
                    "I stopped: this turn asked for approval more times than I will in one go. "
                    "Tell me what you want done and I will do it in steps.",
                )
                return PromptResponse(stop_reason="refusal")
            decision = await self._ask_permission(session, pending)
            try:
                reply = await self.brain.resume(session.thread, decision=decision)
            except ApprovalRequired as again:
                pending = again.payload if isinstance(again.payload, dict) else {}
                continue
            pending = None
            if reply:
                await self._send(session, update_agent_message_text(reply))
                streamed = True

        if failed and not streamed:
            await self._notify(session, "I hit a provider error and did not get a reply out.")
        elif not streamed:
            await self._notify(session, "(no reply)")
        return PromptResponse(stop_reason="end_turn")

    async def _drain(self, session: _Session, text: str) -> tuple[bool, bool, dict | None, bool]:
        """Push one streamed turn out to the client.

        Returns `(streamed, failed, pending, refused)`. The generator is closed
        in `finally` rather than left to the event loop: this method returns
        early when the kernel refuses a turn before calling a provider, and a
        generator left open logs a context error when it is finalized.
        """
        streamed = False
        failed = False
        pending: dict | None = None
        stream = self.brain.stream(text, session_id=session.thread)
        try:
            async for kind, payload in stream:
                if kind == "error":
                    # The kernel refused the turn before calling a provider (the
                    # kill switch, a budget stop): that is a refusal, and saying
                    # so is the difference between a stopped agent and a broken
                    # one.
                    await self._notify(session, str(payload))
                    return streamed, failed, None, True
                if kind == "custom" and isinstance(payload, dict):
                    event = payload.get("kind")
                    if event == "text":
                        delta = str(payload.get("delta", ""))
                        if delta:
                            await self._send(session, update_agent_message_text(delta))
                            streamed = True
                    elif event == "error":
                        failed = True
                    elif event == "approval":
                        pending = payload.get("payload") or {}
                elif kind == "updates" and isinstance(payload, dict):
                    for update in _nodes(payload):
                        for what, event in _tool_events(update):
                            if what == "call":
                                await self._send(
                                    session,
                                    start_tool_call(
                                        str(event.get("id") or uuid.uuid4().hex),
                                        title=str(event.get("name") or "tool"),
                                        kind=tool_kind(str(event.get("name") or "")),
                                        status="in_progress",
                                        raw_input=event.get("args"),
                                    ),
                                )
                            else:
                                call_id = str(event.get("tool_call_id") or "")
                                if not call_id:
                                    continue
                                content = event.get("content")
                                await self._send(
                                    session,
                                    update_tool_call(
                                        call_id,
                                        status="failed" if _failed(content) else "completed",
                                        raw_output=content,
                                    ),
                                )
                        if not streamed:
                            # The reply the kernel settled on. Emitted only when
                            # nothing was streamed, so an editor never sees the
                            # same sentence twice — the CLI's rule, kept for the
                            # same reason.
                            final = _final_text(update)
                            if final:
                                await self._send(session, update_agent_message_text(final))
                                streamed = True
        finally:
            with contextlib.suppress(Exception):
                await stream.aclose()
        return streamed, failed, pending, False

    async def _ask_permission(self, session: _Session, payload: dict) -> str:
        """Turn a kernel interrupt into `session/request_permission`.

        The digest and call id are *not* re-derived here — the kernel's own
        `Envelope` built them, and the same envelope is what `ApprovalGate`
        verifies on resume. The adapter's job is to show the owner what is being
        asked and carry the answer back.
        """
        if self._conn is None:  # pragma: no cover - only a mis-wired harness
            raise RequestError.internal_error({"reason": "no client connection for a permission request"})
        action = str(payload.get("action") or "action")
        call_id = str(payload.get("call_id") or uuid.uuid4().hex)
        side_effecting = bool(payload.get("side_effecting", True))
        # A read that asked to be approved is still a decision, but the wording
        # must not imply a write is about to happen.
        what = "read-only tool" if not side_effecting else "tool"
        description = (
            f"Iris wants to run the {what} `{action}`. Approving allows this one call "
            f"(digest {payload.get('digest', '?')}); the grant cannot be reused."
        )
        options = list(default_permission_options())
        response = await self._conn.request_permission(
            session.session_id,
            tool_call_view(call_id, action, description),
            options,
        )
        outcome = response.outcome
        allowed = isinstance(outcome, AllowedOutcome) and str(getattr(outcome, "option_id", "")) in {
            "approve",
            "approve_for_session",
        }
        log.info(
            "acp approval for %s on session %s: %s",
            action,
            session.session_id,
            "approved" if allowed else "cancelled",
        )
        # The kernel's vocabulary, not ACP's: `resume(decision=...)` accepts
        # `approved` and treats anything else as a refusal, so a dismissed dialog
        # is a refusal rather than an exception.
        return "approved" if allowed else "cancelled"

    # ── notifications ────────────────────────────────────────────────────
    async def _send(self, session: _Session, update: Any) -> None:
        if self._conn is not None:
            await self._conn.session_update(session.session_id, update)

    async def _notify(self, session: _Session, text: str) -> None:
        await self._send(session, update_agent_message_text(text))


def tool_call_view(call_id: str, action: str, description: str = "") -> Any:
    """The `ToolCallUpdate` a permission request carries: the id the owner's
    grant will be bound to, a title that names the action, and the human sentence
    explaining what approving it allows.

    The arguments themselves are deliberately absent — the kernel's `Envelope`
    keeps them out of the payload and binds with a digest instead, so an
    approval dialog can never become a place a secret is displayed.
    """
    return ToolCallUpdate(
        tool_call_id=call_id,
        title=f"iris: {action}",
        kind=tool_kind(action),
        status="pending",
        content=[tool_content(text_block(description))] if description else None,
    )


