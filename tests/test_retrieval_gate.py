"""The model-free retrieval gate: a labelled fixture, real search, no model.

Two-speed gating is the vault's shape: deterministic retrieval metrics run on
every change, and judge suites (which need a key, a budget and a calibration
study before their numbers mean anything) run later. This is the first half, and
it is deliberately the cheap half — the whole run needs no API key and no
network, so it can gate a PR that touches scoring at all.

What it does gate: the *ranking and selection* decisions, end to end, through the
real `MemoryIndex.search` — the hybrid relevance term, recency decay, the
importance multiplier, the JEV-free deterministic order, and MMR diversity
selection. Those are the knobs that decide what actually reaches the prompt.

What it does not gate: the SQL retrieval itself (which rows the vector and FTS
queries pull into the candidate pool) — that is `test_memory_pipeline.py`, and
the two are complementary rather than redundant. A change to either can lose a
memory, and a metrics-only test over canned rows would have missed the second.

The corpus shares the `iris_eval` scratch database with `scripts/eval_lab.py`
(created by `postgres/init.sql` at first boot). Both wipe and re-seed it, which
is what a corpus database is for, and neither touches `iris` or `iris_test`.

The metrics are computed by `iris_ai.eval.retrieval`, which is pure arithmetic
and has its own unit tests, so the maths is gated even where no Postgres exists.

This file fails loudly when the database is absent rather than skipping, like
`test_memory_pipeline.py`: a gate that quietly skips is the dark-test failure
this repo already paid for once. It does mean the handoff's "fast, no DB"
command has to ignore this file too — that is deliberate, not an oversight, and
`test_retrieval_metrics.py` is the part that always runs.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from asyncpg.exceptions import InvalidCatalogNameError

from iris_ai.eval.retrieval import chunk_id, gate_failures, mean_metric, score_queries
from iris_ai.memory.index import ChunkRecord, MemoryIndex
from iris_ai.memory.llm import LLMClient
from iris_ai.memory.provenance import Origin, Provenance

FIXTURE = Path(__file__).parent / "fixtures" / "retrieval_gate.json"

DSN = os.getenv(
    "IRIS_EVAL_POSTGRES_DSN",
    "postgresql+psycopg://iris:iris_dev_password@localhost:5433/iris_eval",
)


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FakeLLM(LLMClient):
    """Deterministic hash embeddings — the same scheme the other DB tests use.

    No model, no key, no network: the gate's numbers depend only on the fixture
    and the scoring code, which is what makes a regression report actionable.
    """

    def __init__(self) -> None:
        self.embedding_dim = 1536

    async def embed(self, texts: list[str], *, timeout: float = 60.0) -> list[list[float]]:
        out = []
        for text in texts:
            vec = np.zeros(self.embedding_dim, dtype=float)
            for token in text.lower().split():
                h = int(hashlib.md5(token.encode()).hexdigest(), 16)
                vec[h % self.embedding_dim] += 1.0
            norm = np.linalg.norm(vec) or 1.0
            out.append((vec / norm).tolist())
        return out

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


@pytest.fixture
async def index():
    """A connected index with a freshly seeded, labelled corpus."""
    fixture = load_fixture()
    idx = MemoryIndex(DSN, FakeLLM())
    try:
        await idx.connect()
    except InvalidCatalogNameError as exc:
        pytest.fail(
            f"the retrieval-gate database does not exist ({exc}). It is the eval "
            "scratch database and is created by postgres/init.sql on first boot; on "
            "an existing volume, create it with:\n"
            "  docker compose exec postgres psql -U iris -d iris -c 'CREATE DATABASE iris_eval;'",
            pytrace=False,
        )
    except OSError as exc:
        pytest.fail(
            f"no Postgres is listening at the eval DSN ({exc}). Start one with:\n"
            "  docker compose up -d postgres",
            pytrace=False,
        )

    async with idx._pool.acquire() as conn:
        await conn.execute("DELETE FROM memory_chunks")
    today = date.today()
    for entry in fixture["corpus"]:
        await idx.upsert_chunks(
            [
                ChunkRecord(
                    path=entry["path"],
                    chunk_index=entry["chunk_index"],
                    content=entry["content"],
                    provenance=Provenance(
                        origin=Origin.AGENT,
                        source="retrieval-gate",
                        observed_at=datetime.combine(
                            today - timedelta(days=entry["days_ago"]), datetime.min.time()
                        ),
                    ),
                    importance=entry["importance"],
                    evergreen=entry["evergreen"],
                )
            ]
        )
    yield idx, fixture
    await idx.close()


async def _rankings(
    idx: MemoryIndex, fixture: dict, *, ablation: set[str] | None = None
) -> dict[str, list[str]]:
    k = fixture["k"]
    out: dict[str, list[str]] = {}
    for case in fixture["queries"]:
        hits = await idx.search(case["query"], top_k=k, mrr_top_k=k, ablation=ablation)
        out[case["query"]] = [chunk_id(hit.path, hit.chunk_index) for hit in hits]
    return out


def _failures(scores, fixture) -> list[str]:
    return gate_failures(
        scores,
        min_recall=fixture["thresholds"]["recall"],
        min_ndcg=fixture["thresholds"]["ndcg"],
    )


async def test_the_labelled_fixture_clears_its_own_thresholds(index):
    """The gate. On failure the message names the query, both metrics and the
    ranking it actually produced, because \"mean recall dropped\" does not tell
    anyone which memory stopped being found."""
    idx, fixture = index
    rankings = await _rankings(idx, fixture)
    relevance = {case["query"]: case["relevant"] for case in fixture["queries"]}

    scores = score_queries(rankings, relevance, k=fixture["k"])
    assert len(scores) == len(fixture["queries"]), "every fixture query must be scored"

    failures = _failures(scores, fixture)
    summary = (
        f"recall@{fixture['k']} {mean_metric(scores, 'recall'):.3f}, "
        f"ndcg@{fixture['k']} {mean_metric(scores, 'ndcg'):.3f}"
    )
    assert not failures, "retrieval gate failed — " + summary + "\n" + "\n".join(failures)


async def test_every_query_in_the_fixture_is_labelled(index):
    """An unlabelled query scores 1.0 for free, so a fixture edit that drops a
    label would silently loosen the gate. Make that impossible instead."""
    _, fixture = index
    labelled = {case["query"] for case in fixture["queries"] if case.get("relevant")}
    assert labelled == {case["query"] for case in fixture["queries"]}


async def test_the_fixture_is_sensitive_to_the_policy_it_claims(index):
    """The gate's own falsification test, per query.

    A gate that cannot fail is decoration, so each query declares in the fixture
    the ablation that must break it — and this asserts that the ablation really
    does. If someone makes the ranking insensitive (a shorter chunk, a reworded
    query, a knob that stops being consulted), the fixture stops gating that
    decision and this test says so instead of leaving a green, hollow gate.

    It is also what proves an ablation name is not a typo: an unknown knob is a
    silent no-op in `search`, and a no-op would leave the query passing.
    """
    idx, fixture = index
    relevance = {case["query"]: case["relevant"] for case in fixture["queries"]}

    declared = [case for case in fixture["queries"] if case.get("must_fail_under")]
    assert declared, "the fixture declares no sensitivity, so the gate could not fail"

    for case in declared:
        for ablation in case["must_fail_under"]:
            rankings = await _rankings(idx, fixture, ablation={ablation})
            scores = score_queries(rankings, relevance, k=fixture["k"])
            failures = _failures(scores, fixture)
            assert any(case["query"] in failure for failure in failures), (
                f"the fixture claims {case['query']!r} is sensitive to {ablation!r}, but it "
                "still clears the gate — that ablation no longer changes this ranking, so "
                f"the query gates nothing. Scores: "
                + ", ".join(
                    f"{s.query!r} recall {s.recall:.2f} ndcg {s.ndcg:.2f}" for s in scores
                )
            )
