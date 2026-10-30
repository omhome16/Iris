"""What a component author imports to test a folder without booting Iris."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from iris_ai.plug import check_folder
from iris_ai.sdk.context import ComponentContext, FixedClock
from iris_ai.sdk.types import (
    CaptureRequest,
    ConsolidationRequest,
    ContextRequest,
)


class InMemoryChannel:
    """A channel with no socket. Tests push messages and read what was sent."""

    name = "memory"
    capabilities = frozenset({"stream", "edit", "approve"})

    def __init__(self) -> None:
        self.inbox: list = []
        self.outbox: list = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def receive(self):
        while self.inbox:
            yield self.inbox.pop(0)

    async def send(self, message) -> str:
        self.outbox.append(message)
        return str(len(self.outbox))

    def push(self, message) -> None:
        self.inbox.append(message)


def check_component(path: str | Path) -> tuple[bool, str]:
    """Import `path` and confirm it implements its kind. Safe to call from tests."""
    return check_folder(Path(path))


def fake_context(kind: str, **grants: object) -> ComponentContext:
    """A v1 context. Ungranted capabilities stay None. No runtime."""
    return ComponentContext(
        api_version="iris/v1",
        kind=kind,
        name="test",
        clock=FixedClock(date(2026, 6, 14)),
        llm=grants.get("llm"),
        memory=grants.get("memory"),
        files=grants.get("files"),
        state=grants.get("state"),
    )


def sample_request(kind: str):
    """One request of the shape `check` probes with."""
    if kind == "capture":
        return CaptureRequest(user_message="We decided to use uv.", reply="Noted.", session_id="probe")
    if kind == "consolidator":
        return ConsolidationRequest(notes="- [5] a fact (note)", curated="# MEMORY.md\n")
    return ContextRequest(message="What did we decide?", session_id="probe", origin="owner")


class HashEmbedder:
    """A deterministic stand-in for an embedding model. Offline and stable.

    `embed` accepts one string or a batch, which is what both a component and
    the SQLite write path call.
    """

    def __init__(self, dims: int = 32) -> None:
        self.dims = dims

    def _vector(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[: self.dims]]

    async def embed(self, text):
        if isinstance(text, (list, tuple)):
            return [self._vector(str(item)) for item in text]
        return self._vector(str(text))

    async def embed_one(self, text: str) -> list[float]:
        return self._vector(text)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


def suite_name(kind: str) -> str:
    """The behaviour suite for a kind. `iris components verify --suite` names it."""
    return f"{kind}-behaviour"


async def _call(fn, *args, **kwargs):
    import inspect

    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _text_of(hit: object) -> str:
    if isinstance(hit, dict):
        return str(hit.get("content") or "")
    return str(getattr(hit, "content", "") or "")


def _score_of(hit: object) -> float:
    if isinstance(hit, dict):
        raw = hit.get("cos", hit.get("score", 0))
    else:
        raw = getattr(hit, "cos", None)
        if raw is None:
            raw = getattr(hit, "score", 0)
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def _as_row(row: object) -> dict:
    if isinstance(row, dict):
        return {"path": str(row.get("path") or ""), "chunk_index": int(row.get("chunk_index") or 0)}
    return {
        "path": str(getattr(row, "path", "") or ""),
        "chunk_index": int(getattr(row, "chunk_index", 0) or 0),
    }


def _connection(backend: object):
    inner = getattr(backend, "_inner", backend)
    return getattr(inner, "_conn", None)


async def memory_conformance(backend: object) -> list[str]:
    """Run the memory behaviour suite. Empty means the store passed.

    The suite writes a temp-backed store when the backend is SQLite: upsert,
    search, idempotent replace, delete, ordered nearest, list, forget, stats,
    and a second connect/close that leaves no connection open.
    """
    from datetime import datetime

    from iris_ai.capabilities.memory_api import REQUIRED
    from iris_ai.eval.world import memory_record

    problems: list[str] = []
    for name in (*REQUIRED, "nearest", "list_chunks", "forget_entry"):
        if not callable(getattr(backend, name, None)):
            problems.append(f"{name} is missing")
    if any(item.endswith(" is missing") and item.split()[0] in REQUIRED for item in problems):
        return problems

    def record(path: str, index: int, content: str):
        return memory_record(path, index, content, observed=datetime.now())

    try:
        await _call(backend.connect)
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"connect failed: {type(exc).__name__}: {exc}"]

    try:
        await _call(backend.upsert_chunks, [record("notes.md", 0, "the owner drinks tea")])
        hits = await _call(backend.search, "tea", top_k=5)
        if not any("tea" in _text_of(hit) for hit in hits or []):
            problems.append("search did not find the upserted record")
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        problems.append(f"upsert/search failed: {type(exc).__name__}: {exc}")

    try:
        one = [record("notes.md", 0, "the owner drinks tea")]
        await _call(backend.replace_file_chunks, "notes.md", one)
        await _call(backend.replace_file_chunks, "notes.md", one)
        stats = await _call(backend.stats)
        if int(stats.get("total_chunks") or 0) != 1:
            problems.append(f"replace was not idempotent: total_chunks={stats.get('total_chunks')}")
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        problems.append(f"replace failed: {type(exc).__name__}: {exc}")

    try:
        await _call(backend.delete_file_chunks, "notes.md")
        stats = await _call(backend.stats)
        if int(stats.get("total_chunks") or 0) != 0:
            problems.append("delete did not remove the file")
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        problems.append(f"delete failed: {type(exc).__name__}: {exc}")

    try:
        await _call(
            backend.upsert_chunks,
            [record("a.md", 0, "red apple"), record("b.md", 0, "blue boat")],
        )
        rows = [_as_row(row) for row in list(await _call(backend.list_chunks) or [])]
        if len(rows) < 2:
            problems.append("list_chunks returned nothing after an upsert")
        near = list(await _call(backend.nearest, "red apple", top_k=2) or [])
        if len(near) < 2:
            problems.append("nearest did not return ordered results")
        else:
            scores = [_score_of(hit) for hit in near]
            if scores != sorted(scores, reverse=True):
                problems.append("nearest is not ordered")
            if "apple" not in _text_of(near[0]):
                problems.append("nearest did not rank the closer text first")
        await _call(backend.forget_entry, "a.md", 0)
        rows = [_as_row(row) for row in list(await _call(backend.list_chunks) or [])]
        if any(row["path"] == "a.md" and row["chunk_index"] == 0 for row in rows):
            problems.append("forget_entry was not honoured")
        stats = await _call(backend.stats)
        if int(stats.get("total_chunks") or 0) != len(rows):
            problems.append(
                f"stats count {stats.get('total_chunks')} does not match list_chunks {len(rows)}"
            )
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        problems.append(f"maintenance failed: {type(exc).__name__}: {exc}")

    try:
        await _call(backend.close)
        await _call(backend.connect)
        await _call(backend.close)
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        problems.append(f"reconnect failed: {type(exc).__name__}: {exc}")
    else:
        if _connection(backend) is not None:
            problems.append("a second connect/close left a connection open")
    return problems


class _RaisingBlock:
    title = "bad"
    priority = 0

    @property
    def text(self) -> str:
        raise RuntimeError("boom")


async def context_conformance(component: object) -> list[str]:
    """A context renders, trims to a budget, and drops a block that raises."""
    from iris_ai.pipeline import drop_raising_blocks, trim_blocks
    from iris_ai.sdk.types import ContextBlock

    problems: list[str] = []
    try:
        result = await _call(component.assemble, sample_request("context"))
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"assemble failed: {type(exc).__name__}: {exc}"]
    render = getattr(result, "render", None)
    if not callable(render):
        return ["assemble did not return a result that renders"]
    try:
        text = render()
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"render failed: {type(exc).__name__}: {exc}"]
    if not isinstance(text, str):
        problems.append("render did not return a string")
    blocks = list(getattr(result, "blocks", ()) or ())
    padded = [
        *blocks,
        ContextBlock(title="keep", text="k" * 40, priority=1),
        ContextBlock(title="drop-me", text="d" * 40, priority=9),
    ]
    trimmed = trim_blocks(padded, max_chars=50)
    if sum(len(getattr(block, "text", "") or "") for block in trimmed) > 50:
        problems.append("trim did not honour the budget")
    if any(getattr(block, "title", "") == "drop-me" for block in trimmed):
        problems.append("trim kept the block with the higher priority number")
    kept = drop_raising_blocks([*blocks, _RaisingBlock(), ContextBlock(title="ok", text="fine", priority=1)])
    if any(getattr(block, "title", "") == "bad" for block in kept):
        problems.append("a raising block was kept")
    if not any(getattr(block, "title", "") == "ok" for block in kept):
        problems.append("a healthy block was dropped")
    return problems


async def capture_conformance(component: object) -> list[str]:
    """extract returns candidates, and redaction drops a secret."""
    from iris_ai.pipeline import redact_candidates
    from iris_ai.sdk.types import MemoryCandidate

    problems: list[str] = []
    try:
        result = await _call(component.extract, sample_request("capture"))
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"extract failed: {type(exc).__name__}: {exc}"]
    if not isinstance(result, list):
        return ["extract did not return a list of candidates"]
    secret = MemoryCandidate(content="api_key=sk-live-abcdefghij")
    kept = redact_candidates([*result, secret])
    if any("sk-live" in str(getattr(item, "content", item)) for item in kept):
        problems.append("a secret survived redaction")
    return problems


async def consolidator_conformance(component: object) -> list[str]:
    """propose returns a plan, and that plan applies to curated text."""
    from iris_ai.eval.world import apply_plan
    from iris_ai.sdk.types import ConsolidationPlan

    try:
        plan = await _call(component.propose, sample_request("consolidator"))
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"propose failed: {type(exc).__name__}: {exc}"]
    if not isinstance(plan, ConsolidationPlan):
        return ["propose did not return a plan"]
    applied = apply_plan("# MEMORY.md\n", plan)
    if not isinstance(applied, str):
        return ["plan did not apply"]
    return []


def persona_conformance(component: object) -> list[str]:
    try:
        text = component.text()
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"text() failed: {type(exc).__name__}: {exc}"]
    if not isinstance(text, str):
        return ["text() did not return a string"]
    return []


def engine_conformance(component: object) -> list[str]:
    try:
        streaming = component.streams()
        planned = component.wants_plan({})
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"engine failed: {type(exc).__name__}: {exc}"]
    if not isinstance(streaming, bool) or not isinstance(planned, bool):
        return ["engine streams() and wants_plan() must return bool"]
    return []


async def channel_conformance(component: object) -> list[str]:
    try:
        if hasattr(component, "connect"):
            await _call(component.connect)
        receipt = await _call(component.send_message, 1, "hello")
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"send_message failed: {type(exc).__name__}: {exc}"]
    if not isinstance(receipt, str):
        return ["send_message did not return a string"]
    return []


def tool_conformance(component: object) -> list[str]:
    try:
        result = component.run({"message": "hi"})
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"run failed: {type(exc).__name__}: {exc}"]
    if result is None:
        return ["run returned nothing"]
    return []


def guard_conformance(component: object) -> list[str]:
    from iris_ai.hooks import HookBus

    bus = HookBus()
    try:
        component.attach(bus)
    except Exception as exc:  # noqa: BLE001 - the suite reports the failure
        return [f"attach failed: {type(exc).__name__}: {exc}"]
    if not bus.subscribers("pre_tool"):
        return ["attach did not subscribe to pre_tool"]
    return []


_RUNNERS = {
    "memory": memory_conformance,
    "context": context_conformance,
    "capture": capture_conformance,
    "consolidator": consolidator_conformance,
    "persona": persona_conformance,
    "engine": engine_conformance,
    "channel": channel_conformance,
    "tool": tool_conformance,
    "guard": guard_conformance,
}


async def run_conformance(kind: str, component: object, *, suite: str = "") -> list[str]:
    """Run one kind's behaviour suite. `suite` must be that kind's suite when set."""
    expected = suite_name(kind)
    if suite and suite not in {expected, kind}:
        return [f"unknown suite {suite}"]
    runner = _RUNNERS.get(kind)
    if runner is None:
        return [f"no suite for {kind}"]
    result = runner(component)
    import inspect

    if inspect.isawaitable(result):
        result = await result
    return list(result)
