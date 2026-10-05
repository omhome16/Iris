"""A scripted harness benchmark. No live model.

It ingests a component, runs it in the host, checks that a component which
imports the kernel fails, and rolls a pin back on a temporary lock.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace


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
