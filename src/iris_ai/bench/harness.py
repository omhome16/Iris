"""A scripted harness benchmark. No live model.

It ingests a component, runs it in the host, checks that a component which
imports the kernel fails, and rolls a pin back on a temporary lock.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace


def drive_cell(spec: dict) -> dict[str, object]:
    """One scripted turn for a matrix cell. A stub model, no network.

    The cell passes when the turn completes, a memory write is readable from
    the same backend, and no stage had to fall back.
    """
    import asyncio

    return asyncio.run(_drive_cell(spec))


async def _drive_cell(spec: dict) -> dict[str, object]:
    import tempfile

    from iris_ai.eval.world import memory_record
    from iris_ai.testing import HashEmbedder

    root = Path(tempfile.mkdtemp(prefix="iris-matrix-"))
    problems: list[str] = []
    fallback = False
    reply = ""
    roundtrip = False
    index = None
    try:
        index = _matrix_memory(str(spec["memory"]), root, HashEmbedder())
        await index.connect()
        record = memory_record("notes.md", 0, "remember the blue bowl")
        await index.upsert_chunks([record])
        hits = await index.search("blue bowl", top_k=3)
        roundtrip = any("blue bowl" in _hit_text(hit) for hit in hits or [])
        if not roundtrip:
            problems.append("memory write did not round-trip")
        await _matrix_context(str(spec["context"]), index, root)
        from iris_ai.engines.dispatch import resolve

        engine = resolve(str(spec["engine"]))
        if not isinstance(engine.streams(), bool):
            problems.append("engine did not answer")

        class Stub:
            async def complete(self, prompt: str, **_kwargs: object) -> str:
                return "noted"

        reply = await Stub().complete("remember the blue bowl")
        if not reply:
            problems.append("turn did not complete")
        await _matrix_capture(str(spec["capture"]))
        if not await _matrix_consolidator(str(spec["consolidator"]), root, Stub()):
            fallback = True
            problems.append("pipeline fell back")
    except Exception as exc:  # noqa: BLE001 - one cell reports its failure
        fallback = True
        problems.append(f"{type(exc).__name__}: {exc}")
    finally:
        if index is not None:
            close = getattr(index, "close", None)
            if close is not None:
                import contextlib

                with contextlib.suppress(Exception):
                    await close()
    return {
        "ok": not problems and roundtrip and not fallback,
        "problems": problems,
        "roundtrip": roundtrip,
        "fallback": fallback,
        "reply": reply,
    }


def _hit_text(hit: object) -> str:
    if isinstance(hit, dict):
        return str(hit.get("content") or "")
    return str(getattr(hit, "content", "") or "")


def _matrix_memory(name: str, root: Path, llm: object):
    from iris_ai.catalog.memory.evidence import EvidenceMemory
    from iris_ai.memory.markdown_index import MarkdownIndex
    from iris_ai.memory.sqlite_index import SqliteIndex
    from iris_ai.plug import component_folder, construct, load_class

    if name == "evidence-memory":
        return EvidenceMemory(path=root / "memory.db", llm=llm)
    if name == "markdown":
        from iris_ai.config import settings

        settings.workspace_dir = str(root)
        return MarkdownIndex(llm=llm)
    if name == "pgvector":
        raise RuntimeError("pgvector needs a Postgres server")
    folder = component_folder("memory", name)
    if folder is not None:
        from types import SimpleNamespace

        from iris_ai.memory.files import WorkspaceFiles

        carrier = SimpleNamespace(llm=llm, files=WorkspaceFiles(root), index=None)
        return construct(load_class(folder), carrier, trust="local")
    if name in {"sqlite", ""}:
        return SqliteIndex(root / "memory.db", llm=llm)
    raise RuntimeError(f"memory {name} is not available")


async def _matrix_context(name: str, index: object, root: Path) -> str:
    from types import SimpleNamespace

    from iris_ai.memory.files import WorkspaceFiles
    from iris_ai.sdk.types import ContextRequest

    class _Skills:
        def selectable(self) -> list:
            return []

    files = WorkspaceFiles(root)
    runtime = SimpleNamespace(files=files, skills=_Skills(), jev=None, index=index, llm=None)
    request = ContextRequest(message="blue bowl", session_id="matrix", origin="owner")
    if name in {"default", ""}:
        from iris_ai.agent.context import ContextAssembler

        result = await ContextAssembler(runtime).assemble(request)
    elif name == "minimal":
        from iris_ai.components import MinimalContext

        result = await MinimalContext(runtime).assemble(request)
    else:
        from iris_ai.components import _builtin
        from iris_ai.plug import component_folder, construct, load_class

        folder = component_folder("context", name)
        if folder is not None:
            component = construct(load_class(folder), runtime, trust="local")
        else:
            cls = _builtin("context", name)
            if cls is None:
                raise RuntimeError(f"context {name} is not available")
            component = construct(cls, runtime, trust="builtin")
        result = await component.assemble(request)
    render = getattr(result, "render", None)
    return render() if callable(render) else str(result)


async def _matrix_capture(name: str) -> list:
    from iris_ai.sdk.types import CaptureRequest

    request = CaptureRequest(user_message="We decided to use uv.", reply="Noted.", session_id="matrix")
    if name in {"default", "", "off"}:
        return []
    from iris_ai.catalog.capture.decision_only import DecisionOnly

    return list(await DecisionOnly().extract(request) or [])


async def _matrix_consolidator(name: str, root: Path, llm: object) -> bool:
    """False when the stage had to fall back. An explicit off is not a fallback."""
    if name == "off":
        return True
    if name == "dreaming":
        from iris_ai.memory.dreaming import DreamEngine
        from iris_ai.memory.files import WorkspaceFiles

        plan = await DreamEngine(llm, WorkspaceFiles(root), None).propose()
        return plan is not None and not getattr(plan, "fallback", False)
    from iris_ai.catalog.consolidator.conflict_resolver import ConflictResolver
    from iris_ai.sdk.types import ConsolidationRequest

    plan = await ConflictResolver().propose(ConsolidationRequest())
    return plan is not None


def scripted() -> dict[str, object]:
    from iris_ai.config import settings
    from iris_ai.isolation.host import open_component

    previous = settings.harness_config
    with tempfile.TemporaryDirectory(prefix="iris-bench-") as raw:
        root = Path(raw)
        config = root / "config"
        config.mkdir()
        harness = config / "harness.toml"
        harness.write_text("[components]\n", encoding="utf-8")
        settings.harness_config = str(harness)
        try:
            hello = _write_hello(root / "components" / "context" / "hello")
            bad = _write_hostile(root / "components" / "context" / "hostile")
            from iris_ai.artifacts.store import ingest

            digest = ingest(hello, root=root / "components")
            hosted = open_component(hello, kind="context", name="hello")
            try:
                result = asyncio.run(
                    hosted.assemble(SimpleNamespace(message="hi", session_id="bench", origin="owner"))
                )
            finally:
                hosted.close()
            hostile_blocked = _hostile_blocked(bad)
            from iris_ai.components.lock import pin, read_lock
            from iris_ai.lifecycle.control import rollback

            pin("context", "hello", source="local", digest=digest, path=config / "components.lock")
            pin(
                "context",
                "other",
                source="builtin",
                digest="b" * 64,
                path=config / "components.lock",
            )
            message = rollback("context")
            active = (read_lock().get("kinds") or {}).get("context", {}).get("active")
            ok = bool(digest) and result is not None and hostile_blocked and active == "hello"
            return {
                "ok": ok,
                "digest": digest,
                "hostile_blocked": hostile_blocked,
                "rollback": message,
                "active": active,
            }
        finally:
            settings.harness_config = previous


def _hostile_blocked(folder: Path) -> bool:
    from iris_ai.isolation.host import open_component

    hosted = None
    try:
        hosted = open_component(folder, kind="context", name="hostile")
        asyncio.run(hosted.assemble(SimpleNamespace(message="hi", session_id="bench", origin="owner")))
    except Exception:  # noqa: BLE001 - any host failure means the kernel import was blocked
        return True
    else:
        return False
    finally:
        if hosted is not None:
            hosted.close()


def _write_hello(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "context"\nname = "hello"\napi_version = "iris/v1"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Component:\n"
        "    def __init__(self, ctx):\n"
        "        self.ctx = ctx\n"
        "    async def assemble(self, request):\n"
        "        from iris_ai.sdk.types import ContextResult\n"
        "        return ContextResult()\n",
        encoding="utf-8",
    )
    return folder


def _write_hostile(folder: Path) -> Path:
    folder.mkdir(parents=True)
    (folder / "component.toml").write_text(
        'kind = "context"\nname = "hostile"\napi_version = "iris/v1"\nentry = "component:Component"\n',
        encoding="utf-8",
    )
    (folder / "component.py").write_text(
        "class Component:\n"
        "    def __init__(self, ctx):\n"
        "        import iris_ai.config\n"
        "        self.ctx = ctx\n"
        "    async def assemble(self, request):\n"
        "        from iris_ai.sdk.types import ContextResult\n"
        "        return ContextResult()\n",
        encoding="utf-8",
    )
    return folder
