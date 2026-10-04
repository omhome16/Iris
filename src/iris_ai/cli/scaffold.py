"""`iris new component` — a file the owner can edit and point the manifest at."""

from __future__ import annotations

from pathlib import Path

_CONTEXT = '''\
"""A context builder. Point `[components] context` at this class."""

from __future__ import annotations


class CustomContext:
    def __init__(self, ctx, **options) -> None:
        self.ctx = ctx

    async def assemble(self, request):
        from iris_ai.sdk.types import ContextBlock, ContextResult

        return ContextResult(blocks=(ContextBlock(title="Extra", text="your context goes here", priority=30),))
'''

_MEMORY = '''\
"""A memory store. Register it as memory_backend or keep editing it."""

from __future__ import annotations

import json
from pathlib import Path


class JsonMemory:
    """Every method the kernel may call. The JSON file is the whole store."""

    def __init__(self, dsn: str = "", llm=None, reranker=None) -> None:
        self.path = Path("memory.json")
        self.rows: list[dict] = []
        if self.path.is_file():
            self.rows = json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self.path.write_text(json.dumps(self.rows), encoding="utf-8")

    async def connect(self) -> None:
        return None

    async def close(self) -> None:
        self._save()

    async def search(self, query: str, **kwargs):
        needle = query.lower()
        return [row for row in self.rows if needle in json.dumps(row).lower()][:5]

    async def escalate(self, query: str, **kwargs):
        return await self.search(query, **kwargs)

    async def stats(self) -> dict:
        return {"backend": "json", "total_chunks": len(self.rows), "location": str(self.path)}

    async def upsert_chunks(self, records: list) -> None:
        self.rows.extend(records)
        self._save()

    async def delete_file_chunks(self, path: str) -> None:
        self.rows = [row for row in self.rows if row.get("path") != path]
        self._save()

    async def replace_file_chunks(self, path: str, records: list) -> None:
        await self.delete_file_chunks(path)
        await self.upsert_chunks(records)

    async def forget_entry(self, path: str, chunk_index: int) -> None:
        self.rows = [
            row for row in self.rows
            if not (row.get("path") == path and row.get("chunk_index") == chunk_index)
        ]
        self._save()

    async def nearest(self, text: str, *, top_k: int = 3) -> list:
        return await self.search(text, top_k=top_k)

    async def list_chunks(self) -> list:
        return list(self.rows)
'''

_PERSONA = '''\
"""A persona source. Point `[components] persona` at this class."""

from __future__ import annotations


class CustomPersona:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    def text(self) -> str:
        return "Be direct. Say when you do not know."
'''

_BODIES = {
    "context": (_CONTEXT, "CustomContext"),
    "memory": (_MEMORY, "JsonMemory"),
    "persona": (_PERSONA, "CustomPersona"),
}


def write_new(kind: str, name: str, dest: Path) -> Path:
    """Write a skeleton and point harness.toml at it."""
    from iris_ai.cli.toml_edit import upsert
    from iris_ai.config import settings

    if kind == "role":
        folder = dest / name
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "role.toml"
        if path.exists():
            raise FileExistsError(path)
        block = (
            f"[roles.{name}]\n"
            f'description = "{name}"\n'
            'prompt = "You are a specialist. Report what you found and where."\n'
            'tools = ["memory_search", "file_read"]\n'
            'model_tier = "cheap"\n'
            "max_steps = 3\n"
        )
        path.write_text(block, encoding="utf-8")
        manifest = Path(settings.harness_config)
        existing = manifest.read_text(encoding="utf-8") if manifest.is_file() else ""
        if f"[roles.{name}]" not in existing:
            manifest.parent.mkdir(parents=True, exist_ok=True)
            manifest.write_text(existing.rstrip() + "\n\n" + block, encoding="utf-8")
        return path

    if kind not in _BODIES:
        from iris_ai.plug import entry_point_hint

        raise ValueError(entry_point_hint(kind))
    body, cls = _BODIES[kind]
    folder = dest / name
    folder.mkdir(parents=True, exist_ok=True)
    init = folder / "__init__.py"
    if not init.exists():
        init.write_text("", encoding="utf-8")
    path = folder / "component.py"
    if path.exists():
        raise FileExistsError(path)
    path.write_text(body, encoding="utf-8")
    dotted = f"{dest.name}.{name}.component:{cls}"
    manifest = Path(settings.harness_config)
    if kind == "memory":
        upsert(manifest, "memory_backend", dotted)
    else:
        upsert(manifest, kind, dotted, table="components")
    return path


def write_component(kind: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = _CONTEXT if kind == "context" else _MEMORY
    if dest.exists():
        raise FileExistsError(dest)
    dest.write_text(body, encoding="utf-8")
    return dest
