"""`iris_ai.harness()` — the library boot path, and the degraded contract.

No Postgres, no network, no keys: the DSN points at a closed port, the LLM is
the suite's deterministic fake, and JEV is forced off so the deterministic
paths run. The one claim these tests exist for is that a session without a
database *says so* — in `mode`, in `degraded_reason`, and in the recall tool's
error — instead of quietly answering from nothing.
"""

from __future__ import annotations

import json

import pytest

from fakes import WizardLLM
from iris_ai.capabilities.models import MODELS
from iris_ai.config import settings
from iris_ai.engine import Harness, harness
from iris_ai.memory.index import MemoryUnavailable
from iris_ai.memory.null_index import NullIndex
from iris_ai.memory.sqlite_index import SqliteIndex
from iris_ai.registry import Registration

# Port 1 is reserved and never listening, so `connect()` is a fast refusal.
DEAD_DSN = "postgresql+psycopg://iris:iris@127.0.0.1:1/iris_test"


@pytest.fixture
def dead_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the engine at a dead database and a throwaway workspace.

    `memory_backend` is pinned to `pgvector` here on purpose. Since Phase 2 the
    *default* backend is SQLite, which needs no service — so "no Postgres" no
    longer implies a degraded session. The degraded contract still exists and
    still matters (it is what an unreachable store must do), so the fixture asks
    for the Postgres backend explicitly rather than relying on it being default.
    """
    monkeypatch.setattr(settings, "postgres_dsn", DEAD_DSN)
    monkeypatch.setattr(settings, "memory_backend", "pgvector")
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_dir", str(tmp_path / "sandbox"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "config" / "memory.db"))
    monkeypatch.setattr(settings, "checkpointer_path", str(tmp_path / "config" / "checkpoints.db"))
    monkeypatch.setattr(settings, "typesafe_api_key", "")
    # The engine builds the model through the capability registry, so the seam
    # to fake is the registry entry, not a symbol on `iris_ai.engine` (which the
    # boot path no longer references directly). Snapshot-and-restore keeps the
    # global registry clean for the next test.
    entries = dict(MODELS._entries)
    entries["litellm"] = Registration(
        kind="model_backend",
        name="litellm",
        factory=lambda ledger=None, **kw: WizardLLM(),
        source="test",
    )
    monkeypatch.setattr(MODELS, "_entries", entries)
    return tmp_path


async def _onboard(brain: Harness) -> None:
    """Walk the wizard to the end so later turns are ordinary chat turns."""
    for answer in ["Hi", "Omar", "warm", "short", "UTC", "4"]:
        await brain.respond(answer, session_id="t1")


async def test_degraded_mode_reports_itself(dead_postgres):
    async with harness(services=False) as brain:
        assert brain.mode == "degraded"
        assert brain.degraded_reason is not None
        assert DEAD_DSN in brain.degraded_reason
        assert isinstance(brain.index, NullIndex)
        assert (await brain.index.stats())["degraded"] is True


async def test_degraded_turn_still_replies_and_journals(dead_postgres):
    """The conversation keeps working: in-memory graph, Markdown still written."""
    async with harness(services=False) as brain:
        first = await brain.respond("Hi", session_id="t1")
        assert first.strip()

        await _onboard(brain)
        reply = await brain.respond("Remind me I prefer green tea", session_id="t1")
        assert reply.strip()

        daily = sorted((brain.files.root / "memory").glob("*.md"))
        assert daily, "the turn must leave evidence in the daily note"
        assert any(line.strip() for path in daily for line in path.read_text(encoding="utf-8").splitlines())


async def test_degraded_recall_says_what_is_missing(dead_postgres):
    """`Search` raises with the fix command — never an empty result set.

    The chat graph's tools node converts any raised tool error into
    `{"ok": false, "error": ...}` for the model (see `ChatGraph._tools`), so
    the agent is told the truth in words rather than hallucinating around
    silence.
    """
    from iris_ai.agent.tools import run_memory_search

    async with harness(services=False) as brain:
        with pytest.raises(MemoryUnavailable) as exc:
            await run_memory_search(brain.runtime, "green tea")
        assert "docker compose up -d postgres" in str(exc.value)
        assert DEAD_DSN in str(exc.value)


async def test_degraded_turn_api_is_the_shared_one(dead_postgres):
    """`respond`/`stream` delegate to the same chat graph the API uses."""
    async with harness(services=False) as brain:
        kinds = [kind async for kind, _payload in brain.stream("Hi", session_id="t2")]
        assert "custom" in kinds or "updates" in kinds


async def test_require_mode_still_fails_at_boot(dead_postgres):
    """The API's contract: a missing database is a boot failure, not a session."""
    with pytest.raises(Exception) as exc:  # any connection error is the contract
        async with harness(postgres="require", services=False):
            pass
    assert not isinstance(exc.value, MemoryUnavailable)


@pytest.fixture
def no_services(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """A fresh clone's world: no Postgres, no keys, default backends."""
    monkeypatch.setattr(settings, "postgres_dsn", DEAD_DSN)
    monkeypatch.setattr(settings, "memory_backend", "sqlite")
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_dir", str(tmp_path / "sandbox"))
    monkeypatch.setattr(settings, "sqlite_path", str(tmp_path / "config" / "memory.db"))
    monkeypatch.setattr(settings, "checkpointer_backend", "auto")
    monkeypatch.setattr(settings, "checkpointer_path", str(tmp_path / "config" / "checkpoints.db"))
    monkeypatch.setattr(settings, "typesafe_api_key", "")

    entries = dict(MODELS._entries)
    entries["litellm"] = Registration(
        kind="model_backend",
        name="litellm",
        factory=lambda ledger=None, **kw: WizardLLM(),
        source="test",
    )
    monkeypatch.setattr(MODELS, "_entries", entries)
    return tmp_path


async def test_a_clone_with_no_database_boots_full(no_services):
    """Phase 2's onboarding claim: no daemon, and still a working mind."""
    async with harness(services=False) as brain:
        assert brain.mode == "full"
        assert brain.degraded_reason is None
        assert isinstance(brain.index, SqliteIndex)
        assert (await brain.index.stats())["vectors"] is False  # no embedding key


async def test_a_clone_with_no_database_persists_threads(no_services):
    """The checkpointer falls back to SQLite, so a thread survives the process."""
    async with harness(services=False) as brain:
        assert brain.checkpointer == "sqlite"


async def test_a_clone_with_no_database_can_still_recall(no_services):
    """The Phase 2 claim, end to end: a note written to the Markdown is
    recallable with no database and no embedding key.

    It writes the daily note and reindexes it exactly the way the capture pass
    does, rather than relying on a model to decide to capture — the point here
    is that the *store* answers, not that a fake agreed to remember.
    """
    async with harness(services=False) as brain:
        day = brain.files.today()
        brain.files.append_daily("(note) Omar prefers jasmine tea", day=day, stamp=False)
        await brain.runtime.reindexer.index_daily_note(f"memory/{day.isoformat()}.md")

        hits = await brain.index.search("jasmine", top_k=5)
        assert hits, "the note just written must be recallable"
        assert any("jasmine" in h.content.lower() for h in hits)


async def test_an_explicit_remember_is_recallable_with_no_database(no_services):
    """Same claim through the tool the owner actually uses."""
    from iris_ai.agent.tools import run_memory_search

    async with harness(services=False) as brain:
        day = brain.files.today()
        brain.files.append_daily("(note) my passport expires in March", day=day, stamp=False)
        await brain.runtime.reindexer.index_daily_note(f"memory/{day.isoformat()}.md")
        result = await run_memory_search(brain.runtime, "passport")
        assert "passport" in str(result).lower()


async def test_a_dead_postgres_is_detected_without_asking_psycopg():
    """`auto` asks the kernel before it asks the driver.

    This is what keeps the fallback *fast* on Windows: psycopg would spend its
    whole connect deadline discovering a refusal the kernel already handed to
    any blocking `connect()`. The probe has to be honest in both directions, so
    the same port is checked while it listens and after it closes.
    """
    import socket

    from iris_ai.engine import _postgres_accepts_connections

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        dsn = f"postgresql+psycopg://iris:iris@127.0.0.1:{port}/iris_test"
        assert await _postgres_accepts_connections(dsn) is True
    assert await _postgres_accepts_connections(dsn) is False
    # No TCP endpoint to probe (a unix socket, or libpq's own default): the
    # driver's answer is the only honest one, so the tier is not skipped.
    assert await _postgres_accepts_connections("postgresql:///iris") is True


def test_the_checkpointer_dsn_carries_a_connect_deadline(monkeypatch: pytest.MonkeyPatch):
    """A boot must not wait psycopg's 130-second default before it degrades.

    That default is why the Postgres tier needs a deadline of its own: on
    Windows an unreachable server is reported only in the *exceptional* fd set,
    which an asyncio selector loop never watches, so psycopg's connect poll runs
    to its deadline instead of failing at once. `connect_timeout` is what turns
    "130 seconds of nothing, on every boot" into "degrade now".
    """
    from iris_ai.engine import _checkpointer_dsn

    monkeypatch.setattr(settings, "postgres_connect_timeout", 5.0)
    dsn = _checkpointer_dsn(DEAD_DSN)
    assert dsn.startswith("postgresql://")  # psycopg's spelling, not SQLAlchemy's
    assert "connect_timeout=5" in dsn

    # psycopg reads <=0 as "no timeout" and quietly uses its 130s default again,
    # so the floor is real: clamp rather than pass the surprise along.
    monkeypatch.setattr(settings, "postgres_connect_timeout", 0)
    assert "connect_timeout=2" in _checkpointer_dsn(DEAD_DSN)

    # A parameter already in the DSN is the operator's last word — never doubled.
    explicit = DEAD_DSN + "?sslmode=disable&connect_timeout=30"
    assert "connect_timeout=30" in _checkpointer_dsn(explicit)
    assert _checkpointer_dsn(explicit).count("connect_timeout") == 1


def test_public_surface_works_without_a_harness_module():
    """`iris_ai.harness()` is a callable on the package, not a submodule."""
    import iris_ai
    import iris_ai.engine  # importing the implementation must not clobber the name

    assert callable(iris_ai.harness)
    assert iris_ai.Harness.__name__ == "Harness"
    with pytest.raises(AttributeError):
        _ = iris_ai.does_not_exist


async def test_stream_payloads_are_json_serialisable(dead_postgres):
    """The CLI renders these; they must survive the same shape the API sends."""
    async with harness(services=False) as brain:
        async for kind, payload in brain.stream("Hi", session_id="t3"):
            if kind == "custom":
                json.dumps(payload)
