"""Conversation threads. SQLite by default, memory for tests, Postgres optional.

A thread is the message list plus the small bits of turn state that have to
survive a restart (loaded tools, the summary, a pending approval).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Protocol

from iris_ai.kernel.messages import Msg, msg_from_dict, msg_to_dict


class ThreadStore(Protocol):
    async def load(self, thread_id: str) -> dict: ...

    async def save(self, thread_id: str, state: dict) -> None: ...

    async def list_threads(self) -> list[str]: ...

    async def pending_approval(self, thread_id: str) -> str | None: ...

    async def close(self) -> None: ...


def approval_prompt(state: dict | None) -> str | None:
    """The question a paused turn is waiting on, or nothing if the turn finished."""
    pending = (state or {}).get("__interrupt__") or []
    if not pending:
        return None
    first = pending[0]
    if isinstance(first, dict):
        value = first.get("value", first)
    else:
        value = getattr(first, "value", None)
    if isinstance(value, dict):
        text = _describe_approval(value)
    else:
        text = "" if value is None else str(value)
    text = text.strip()
    return text or None


def _describe_approval(value: dict) -> str:
    """What the owner is being asked to allow, beyond the tool name."""
    action = str(value.get("action") or value.get("summary") or "approval")
    lines = [action]
    if value.get("component"):
        lines.append(f"component: {value['component']}")
    files = value.get("files")
    if isinstance(files, list) and files:
        lines.append("files: " + ", ".join(str(item) for item in files))
    digest = str(value.get("component_digest") or value.get("digest") or "")
    if digest:
        lines.append(f"digest: {digest[:16]}")
    if value.get("active") or value.get("previous"):
        lines.append(f"from {value.get('active') or '(none)'} to {value.get('previous') or '(built-in)'}")
    hit = value.get("hit")
    if hit:
        lines.append(str(hit))
    elif value.get("query"):
        lines.append(f"query: {value['query']}")
    permissions = value.get("permissions")
    if isinstance(permissions, list) and permissions:
        lines.append("permissions: " + ", ".join(str(item) for item in permissions))
    elif isinstance(permissions, list):
        lines.append("permissions: (none declared)")
    if value.get("execution"):
        lines.append(str(value["execution"]))
    preview = value.get("preview")
    if isinstance(preview, str) and preview.strip():
        lines.append("source:")
        lines.append(preview.strip())
    change = value.get("changes")
    if change:
        lines.append(str(change))
    elif action == "component_activate":
        lines.append("will move the staged component into place and select it")
    elif action == "component_rollback":
        lines.append("will switch back to the previous component")
    return "\n".join(lines)


def _copy_state(state: dict) -> dict:
    """A shallow copy that does not share the message list."""
    copied = dict(state)
    copied["messages"] = list(state.get("messages") or [])
    return copied


class MemoryThreadStore:
    """In-process threads. They vanish when the process does."""

    def __init__(self) -> None:
        self._threads: dict[str, dict] = {}

    async def load(self, thread_id: str) -> dict:
        state = self._threads.get(thread_id)
        return _copy_state(state) if state else {}

    async def save(self, thread_id: str, state: dict) -> None:
        self._threads[thread_id] = _copy_state(state)

    async def list_threads(self) -> list[str]:
        return sorted(self._threads)

    async def pending_approval(self, thread_id: str) -> str | None:
        return approval_prompt(await self.load(thread_id))

    async def close(self) -> None:
        return None


def _encode(state: dict) -> str:
    payload = dict(state)
    messages = []
    for message in state.get("messages") or []:
        if isinstance(message, Msg):
            messages.append(msg_to_dict(message))
        elif isinstance(message, dict):
            messages.append(message)
        else:
            messages.append(msg_to_dict(Msg(type=getattr(message, "type", "ai"), content=getattr(message, "content", ""))))
    payload["messages"] = messages
    interrupt = payload.get("__interrupt__")
    if interrupt:
        payload["__interrupt__"] = [
            item.value if hasattr(item, "value") else item for item in interrupt
        ]
    if isinstance(payload.get("active_skills"), tuple):
        payload["active_skills"] = list(payload["active_skills"])
    if isinstance(payload.get("loaded_tools"), tuple):
        payload["loaded_tools"] = list(payload["loaded_tools"])
    return json.dumps(payload, ensure_ascii=False, default=str)


def _decode(raw: str) -> dict:
    payload = json.loads(raw)
    payload["messages"] = [msg_from_dict(item) for item in payload.get("messages") or []]
    if isinstance(payload.get("active_skills"), list):
        payload["active_skills"] = tuple(payload["active_skills"])
    if isinstance(payload.get("loaded_tools"), list):
        payload["loaded_tools"] = tuple(payload["loaded_tools"])
    return payload


class SqliteThreadStore:
    """One SQLite file. Threads survive a restart and need no server."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS threads (id TEXT PRIMARY KEY, state TEXT NOT NULL)"
        )
        self._conn.commit()

    async def load(self, thread_id: str) -> dict:
        row = self._conn.execute("SELECT state FROM threads WHERE id = ?", (thread_id,)).fetchone()
        return _decode(row[0]) if row else {}

    async def save(self, thread_id: str, state: dict) -> None:
        self._conn.execute(
            "INSERT INTO threads (id, state) VALUES (?, ?) "
            "ON CONFLICT(id) DO UPDATE SET state = excluded.state",
            (thread_id, _encode(state)),
        )
        self._conn.commit()

    async def list_threads(self) -> list[str]:
        rows = self._conn.execute("SELECT id FROM threads ORDER BY id").fetchall()
        return [row[0] for row in rows]

    async def pending_approval(self, thread_id: str) -> str | None:
        return approval_prompt(await self.load(thread_id))

    async def close(self) -> None:
        self._conn.close()


class PostgresThreadStore:
    """Shared threads for more than one process. Requires the postgres extra."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._pool = None

    async def connect(self) -> None:
        import asyncpg

        # asyncpg wants a plain postgresql:// DSN, not the SQLAlchemy form.
        dsn = self.dsn.replace("postgresql+psycopg://", "postgresql://").replace(
            "postgresql+asyncpg://", "postgresql://"
        )
        self._pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS iris_threads (id TEXT PRIMARY KEY, state TEXT NOT NULL)"
            )

    async def load(self, thread_id: str) -> dict:
        async with self._pool.acquire() as conn:
            raw = await conn.fetchval("SELECT state FROM iris_threads WHERE id = $1", thread_id)
        return _decode(raw) if raw else {}

    async def save(self, thread_id: str, state: dict) -> None:
        encoded = _encode(state)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO iris_threads (id, state) VALUES ($1, $2) "
                "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state",
                thread_id,
                encoded,
            )

    async def list_threads(self) -> list[str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT id FROM iris_threads ORDER BY id")
        return [row["id"] for row in rows]

    async def pending_approval(self, thread_id: str) -> str | None:
        return approval_prompt(await self.load(thread_id))

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()


def coerce_store(candidate: Any) -> ThreadStore:
    """Accept a ThreadStore. Anything else (including a removed checkpointer) is memory."""
    if candidate is not None and hasattr(candidate, "load") and hasattr(candidate, "save"):
        return candidate
    return MemoryThreadStore()
