"""Cost ledger: append-only JSONL, rollups, and LLM usage recording."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iris.ledger import CostLedger, estimate_cost
from iris.memory.llm import LLMClient


def test_estimate_cost_known_and_unknown():
    assert estimate_cost("gemini/gemini-2.5-flash", 1_000_000, 0) == 0.30
    assert estimate_cost("gemini/gemini-2.5-flash", 0, 1_000_000) == 2.50
    assert estimate_cost("weird/model", 5_000_000, 0) == 0.0  # unknown → free fallback


def test_ledger_append_and_totals(tmp_path: Path):
    path = tmp_path / "config" / "llm_calls.jsonl"
    ledger = CostLedger(path)
    ledger.record(model="gemini/gemini-2.5-flash", tier="cheap", prompt_tokens=1_000_000, completion_tokens=100_000)
    ledger.record(model="gemini/gemini-2.5-flash", tier="strong", prompt_tokens=1_000_000, completion_tokens=100_000)
    ledger.record(model="gemini/gemini-2.5-flash", tier="cheap", prompt_tokens=1_000_000, completion_tokens=100_000)

    assert path.exists()
    lines = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 3
    assert lines[0]["cost"] == 0.30 + 0.25

    totals = CostLedger(path).totals()
    assert totals["requests"] == 3
    assert totals["cost"] == pytest.approx(3 * (0.30 + 0.25), abs=1e-4)
    assert totals["prompt_tokens"] == 3_000_000

    daily = CostLedger(path).daily_totals()
    assert len(daily) == 1
    # ledger converts timestamps to the machine's local day
    assert daily[0]["day"] == datetime.now().date().isoformat()
    assert daily[0]["cost"] == pytest.approx(3 * 0.55, abs=1e-4)

    weekly = CostLedger(path).weekly_totals()
    assert weekly[0]["week"].startswith(str(datetime.now(timezone.utc).isocalendar().year))


def test_ledger_unknown_model_records_zero_cost(tmp_path: Path):
    ledger = CostLedger(tmp_path / "calls.jsonl")
    ledger.record(model="custom/thing", tier="strong", prompt_tokens=500, completion_tokens=50)
    totals = ledger.totals()
    assert totals["requests"] == 1
    assert totals["cost"] == 0.0


def test_ledger_cache_tokens_and_hit_rate(tmp_path: Path):
    path = tmp_path / "calls.jsonl"
    ledger = CostLedger(path)
    ledger.record(model="gemini/gemini-2.5-flash", tier="strong", prompt_tokens=400, completion_tokens=10, cached_tokens=600)
    ledger.record(model="gemini/gemini-2.5-flash", tier="strong", prompt_tokens=400, completion_tokens=10, cached_tokens=600)
    ledger.record(model="gemini/gemini-2.5-flash", tier="cheap", prompt_tokens=100, completion_tokens=5)

    totals = ledger.totals()
    assert totals["cached_tokens"] == 1200
    assert totals["cache_hit_rate"] == pytest.approx(1200 / (900 + 1200), abs=1e-4)
    daily = ledger.daily_totals()
    assert daily[0]["cache_hit_rate"] == totals["cache_hit_rate"]


async def test_llm_extracts_cached_tokens_both_provider_styles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import iris.memory.llm as llm_mod

    class FakeUsage:
        prompt_tokens = 500
        completion_tokens = 20
        cachedContentTokenCount = 300  # Gemini-style

    async def fake_acompletion(**kwargs):
        return type("R", (), {
            "choices": [type("C", (), {"message": type("M", (), {"content": "ok", "tool_calls": None})()})()],
            "usage": FakeUsage(),
        })()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    ledger = CostLedger(tmp_path / "calls.jsonl")
    client = LLMClient(ledger=ledger)
    await client.complete([{"role": "user", "content": "x"}], tier="cheap")
    assert ledger.totals()["cached_tokens"] == 300
    assert ledger.totals()["cache_hit_rate"] == 300 / 800

    class OpenAiUsage:
        prompt_tokens = 500
        completion_tokens = 20
        prompt_tokens_details = type("D", (), {"cached_tokens": 450})()

    async def fake2(**kwargs):
        return type("R", (), {
            "choices": [type("C", (), {"message": type("M", (), {"content": "ok", "tool_calls": None})()})()],
            "usage": OpenAiUsage(),
        })()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake2)
    await client.complete([{"role": "user", "content": "x"}], tier="cheap")
    assert ledger.totals()["cached_tokens"] == 750


def test_ledger_corrupt_line_skipped(tmp_path: Path):
    path = tmp_path / "calls.jsonl"
    path.write_text("not json\n", encoding="utf-8")
    ledger = CostLedger(path)
    ledger.record(model="gemini/gemini-2.5-flash", tier="cheap", prompt_tokens=100, completion_tokens=10)
    assert ledger.totals()["requests"] == 1


async def test_llm_records_usage_when_ledger_attached(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import iris.memory.llm as llm_mod

    calls: list[dict] = []

    class FakeResp:
        def __init__(self, content=None, tool_calls=None):
            self.choices = [type("C", (), {"message": type("M", (), {
                "content": content,
                "tool_calls": tool_calls,
            })()})()]
            self.usage = type("U", (), {"prompt_tokens": 111, "completion_tokens": 22})()

    async def fake_acompletion(**kwargs):
        calls.append(kwargs)
        return FakeResp(content="hi")

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)

    ledger = CostLedger(tmp_path / "calls.jsonl")
    client = LLMClient(ledger=ledger)
    await client.complete([{"role": "user", "content": "x"}], tier="cheap")
    await client.complete_with_tools([{"role": "user", "content": "y"}], tools=[])

    assert ledger.totals()["requests"] == 2
    assert ledger.totals()["prompt_tokens"] == 222


async def test_llm_no_ledger_is_silent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import iris.memory.llm as llm_mod

    async def fake_acompletion(**kwargs):
        return type("R", (), {
            "choices": [type("C", (), {"message": type("M", (), {"content": "ok", "tool_calls": None})()})()],
            "usage": None,
        })()

    monkeypatch.setattr(llm_mod.litellm, "acompletion", fake_acompletion)
    client = LLMClient(ledger=None)
    out = await client.complete([{"role": "user", "content": "x"}])
    assert out == "ok"