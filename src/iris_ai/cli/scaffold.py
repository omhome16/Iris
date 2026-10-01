"""`iris new component` — a file the owner can edit and point the manifest at."""

from __future__ import annotations

from pathlib import Path

_CONTEXT = '''\
"""A context builder. Point `[components] context` at this class."""

from __future__ import annotations


class CustomContext:
    def __init__(self, runtime) -> None:
        self.runtime = runtime

    async def assemble_turn(self, user_message: str, *, session_id: str) -> tuple[str, list[str]]:
        # Start from the built-in prefix, then add your own block.
        from iris_ai.agent.context import ContextAssembler

        text, skills = await ContextAssembler(self.runtime).assemble_turn(
            user_message, session_id=session_id
        )
        return text + "\\n\\n## Extra\\n(your context goes here)", skills
'''

_MEMORY = '''\
"""A memory store sketch. Register it and set `[components]` or memory_backend."""

from __future__ import annotations

import json
from pathlib import Path


class JsonMemory:
    """Remembers chunks in one JSON file. Enough to see the seam; not the default."""

    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "memory.json"
        self.rows: list[dict] = []
        if self.path.is_file():
            self.rows = json.loads(self.path.read_text(encoding="utf-8"))

    async def search(self, query: str, **kwargs):
        needle = query.lower()
        return [row for row in self.rows if needle in json.dumps(row).lower()][:5]
'''


def write_component(kind: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = _CONTEXT if kind == "context" else _MEMORY
    if dest.exists():
        raise FileExistsError(dest)
    dest.write_text(body, encoding="utf-8")
    return dest
