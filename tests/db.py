"""The scratch databases the suite owns, created on demand.

Two test modules need a real Postgres and neither may touch the owner's memory:
`tests/test_memory_pipeline.py` writes to a dedicated `iris_test`, and the
retrieval gate shares the `iris_eval` corpus database with `scripts/eval_lab.py`.
Both used to require someone to run `CREATE DATABASE` first — a CI step, or a
human reading the failure message — and a missing one surfaced as five cryptic
asyncpg errors.

Creating them here is deliberate rather than convenient. These are *scratch*
databases: the entire reason they exist separately is that the suite may write
and wipe them, so the thing being created is the thing the suite is already
allowed to destroy. A test that cannot run until an operator types a DDL command
is a test that stays dark, which is the failure this suite exists to prevent.

What is *not* absorbed here is "no server at all": the caller still fails loudly
with the command to start one, because that is a real misconfiguration.
"""

from __future__ import annotations

from urllib.parse import unquote, urlsplit

import asyncpg

#: The database every Postgres server always has, used only to reach the server.
MAINTENANCE_DB = "postgres"


def split_dsn(dsn: str) -> tuple[dict[str, object], str]:
    """`(asyncpg connect kwargs for the maintenance db, database name)`.

    Hand-parsed instead of using SQLAlchemy's `make_url`, because SQLAlchemy is a
    transitive dependency (via `langgraph-checkpoint-postgres`) and a test helper
    should not be the thing that starts relying on one. Credentials are
    percent-decoded here, since a DSN may encode them.
    """
    plain = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
    parts = urlsplit(plain)
    name = parts.path.lstrip("/") or MAINTENANCE_DB
    kwargs: dict[str, object] = {"database": MAINTENANCE_DB}
    if parts.hostname:
        kwargs["host"] = parts.hostname
    if parts.port:
        kwargs["port"] = parts.port
    if parts.username:
        kwargs["user"] = unquote(parts.username)
    if parts.password:
        kwargs["password"] = unquote(parts.password)
    return kwargs, name


async def ensure_database(dsn: str) -> bool:
    """Create the database `dsn` names if it is absent. Returns whether it created it.

    Connects to the maintenance database, so this works when the database being
    asked for is exactly the one that does not exist yet.
    """
    kwargs, name = split_dsn(dsn)
    conn = await asyncpg.connect(**kwargs)  # type: ignore[arg-type]
    try:
        if await conn.fetchval("select 1 from pg_database where datname = $1", name):
            return False
        # Identifiers cannot be bound as parameters, so the name is interpolated —
        # it comes from configuration, never from a request, and is quoted.
        await conn.execute(f'CREATE DATABASE "{name}"')
        return True
    finally:
        await conn.close()
