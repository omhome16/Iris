"""Dict hits and a null completion. Both showed up with the swapped catalog."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iris_ai.agent.tools import memory_result_payload, run_memory_search
from iris_ai.api import ForgetRequest, app, forget_search
from iris_ai.capabilities.memory_api import HitRecord, as_hit
from iris_ai.memory.index import MemoryHit
from iris_ai.memory.provenance import Origin


class _DictIndex:
    async def search(self, query: str, **kwargs):
        del query, kwargs
        return [
            {
                "content": "the owner keeps notes in a graph",
                "path": "MEMORY.md",
                "chunk_index": 2,
                "score": 0.42,
                "origin": "owner",
                "via": "related",
            }
        ]


class _Files:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str]] = []

    def record_recall_feedback(self, path: str, content: str) -> None:
        self.seen.append((path, content))


async def test_dict_hits_survive_search_and_the_model_payload(monkeypatch) -> None:
    from iris_ai.config import settings

    monkeypatch.setattr(settings, "recall_feedback_enabled", True)
    files = _Files()
    runtime = SimpleNamespace(index=_DictIndex(), files=files)
    hits = await run_memory_search(runtime, "graph", top_k=5)
    assert len(hits) == 1
    hit = hits[0]
    assert isinstance(hit, HitRecord)
    assert hit.path == "MEMORY.md"
    assert hit["content"] == "the owner keeps notes in a graph"
    assert hit.get("score") == 0.42
    assert hit.get("missing", "absent") == "absent"
    payload = memory_result_payload(hit)
    assert payload["content"] == "the owner keeps notes in a graph"
    assert payload["path"] == "MEMORY.md"
    assert payload["score"] == 0.42
    assert payload["origin"] == "owner"
    assert payload["lane"] == "default"
    assert files.seen == [("MEMORY.md", "the owner keeps notes in a graph")]


def test_a_real_memory_hit_is_not_rebuilt() -> None:
    from datetime import date

    hit = MemoryHit(
        content="tea",
        path="MEMORY.md",
        score=1.0,
        importance=1.0,
        origin=Origin.OWNER,
        observed_at=date.today(),
        evergreen=False,
    )
    assert as_hit(hit) is hit


async def test_forget_route_filters_dict_hits(monkeypatch: pytest.MonkeyPatch) -> None:
    class Index:
        async def search(self, query: str, **kwargs):
            del query, kwargs
            return [
                {"content": "keep", "path": "MEMORY.md", "chunk_index": 4, "score": 0.2, "origin": "owner"},
                {"content": "daily", "path": "memory/2026-01-01.md", "chunk_index": 0, "score": 0.9},
                {"content": "no index", "path": "MEMORY.md"},
            ]

    monkeypatch.setattr(app.state, "runtime", SimpleNamespace(index=Index()), raising=False)
    data = await forget_search(ForgetRequest(query="keep"), _token=None)
    assert data["candidates"] == [
        {
            "content": "keep",
            "path": "MEMORY.md",
            "chunk_index": 4,
            "score": 0.2,
            "origin": "owner",
        }
    ]


async def test_complete_raises_on_null_content(monkeypatch: pytest.MonkeyPatch) -> None:
    import iris_ai.memory.llm as llm_mod
    from iris_ai.config import settings
    from iris_ai.memory.llm import LLMClient, LLMError

    class Resp:
        def __init__(self) -> None:
            self.choices = [
                type(
                    "C",
                    (),
                    {
                        "finish_reason": "length",
                        "message": type("M", (), {"content": None})(),
                    },
                )()
            ]
            self.usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 100})()

    async def fake_acompletion(**kwargs):
        del kwargs
        return Resp()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    monkeypatch.setattr(
        type(settings),
        "llm_candidates",
        lambda self, tier="strong": [("test", "nemotron-test", {})],
    )
    client = LLMClient(ledger=None)
    with pytest.raises(LLMError, match="nemotron-test") as raised:
        await client.complete([{"role": "user", "content": "hi"}], tier="cheap", max_attempts=1)
    assert "finish_reason=length" in str(raised.value)
    assert "no content" in str(raised.value)
