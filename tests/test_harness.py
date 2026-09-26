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
from iris_ai.config import settings
from iris_ai.engine import Harness, harness
from iris_ai.memory.index import MemoryUnavailable
from iris_ai.memory.null_index import NullIndex

# Port 1 is reserved and never listening, so `connect()` is a fast refusal.
DEAD_DSN = "postgresql+psycopg://iris:iris@127.0.0.1:1/iris_test"


@pytest.fixture
def dead_postgres(monkeypatch: pytest.MonkeyPatch, tmp_path):
    """Point the engine at a dead database and a throwaway workspace."""
    monkeypatch.setattr(settings, "postgres_dsn", DEAD_DSN)
    monkeypatch.setattr(settings, "workspace_dir", str(tmp_path))
    monkeypatch.setattr(settings, "sandbox_dir", str(tmp_path / "sandbox"))
    monkeypatch.setattr(settings, "typesafe_api_key", "")
    monkeypatch.setattr("iris_ai.engine.LLMClient", lambda ledger=None: WizardLLM())
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
