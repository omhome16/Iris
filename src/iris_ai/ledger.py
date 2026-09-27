"""Cost ledger — append-only JSONL of LLM usage, with daily/weekly rollups.

Every provider call (complete / complete_with_tools / embed) records its
usage line to `workspace/config/llm_calls.jsonl` when a ledger is attached
to the LLMClient. Prices are per-1M-token estimates per model; unknown
models fall back to zero (never fail a call because of accounting).
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("iris_ai.ledger")

# USD per 1M tokens (input, output), keyed by model name.
#
# Two kinds of entry, and the difference is reported rather than hidden:
# - a priced model has an explicit tuple above zero
# - a zero entry is either a genuinely free model (openrouter `:free`, local
#   ollama, Groq free tier) or an unknown one. `unpriced_models()` in the
#   totals makes that distinction visible, so `/costs` can never present a
#   $0.00 total as if it were a measured cost.
PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "gemini/gemini-2.5-flash": (0.30, 2.50),
    "gemini/gemini-2.5-flash-preview-08-17": (0.30, 2.50),
    "gemini/gemini-2.0-flash": (0.10, 0.40),
    "gemini/text-embedding-004": (0.0, 0.0),
    # current configured defaults (config.py) — estimates
    "gemini/gemini-3.5-flash": (0.30, 2.50),
    "gemini/gemini-3.1-flash-lite": (0.10, 0.40),
    "gemini/gemini-embedding-001": (0.15, 0.0),
    # JEV / TypeSafe System One — input-only billing, $42 per billion tokens
    # (https://docs.typesafe.ai/models).
    "jev-latest": (0.042, 0.0),
    "jev-1.13.0": (0.042, 0.0),
    # free tiers + local: priced at zero on purpose, not merely unknown.
    # Groq ids verified against console.groq.com/docs/models on 2026-09-21;
    # gpt-oss is on Groq's free tier, so zero here is a real free-tier price,
    # not a missing entry (the old qwen/compound ids were both dead ones).
    "groq/openai/gpt-oss-120b": (0.0, 0.0),
    "groq/openai/gpt-oss-20b": (0.0, 0.0),
    "groq/whisper-large-v3-turbo": (0.0, 0.0),
    "openrouter/nvidia/nemotron-3-super-120b-a12b:free": (0.0, 0.0),
    "openrouter/nvidia/nemotron-nano-9b-v2:free": (0.0, 0.0),
    "ollama/qwen2.5-coder:3b": (0.0, 0.0),
    "ollama/nomic-embed-text": (0.0, 0.0),
}


def unpriced_models(rows: list[dict]) -> list[str]:
    """Models that appeared in the ledger without an entry in the price table.

    Surfaced so a zero-cost rollup is never mistaken for a measured one.
    """
    return sorted({str(r.get("model", "")) for r in rows if str(r.get("model", "")) not in PRICE_PER_1M})


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICE_PER_1M.get(model, (0.0, 0.0))
    return prompt_tokens * in_price / 1_000_000 + completion_tokens * out_price / 1_000_000


def _cache_hit_rate(prompt_tokens: int, cached_tokens: int) -> float:
    total = prompt_tokens + cached_tokens
    return round(cached_tokens / total, 4) if total else 0.0


class CostLedger:
    """Append-only JSONL store at workspace/config/llm_calls.jsonl."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        *,
        model: str,
        tier: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = {
                "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                "model": model,
                "tier": tier,
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "cached_tokens": int(cached_tokens),
                "cost": round(estimate_cost(model, prompt_tokens, completion_tokens), 6),
            }
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line) + "\n")
        except Exception as exc:  # noqa: BLE001 - accounting must never break a call
            log.warning("cost ledger write failed: %s", exc)

    def _read(self) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        try:
            for raw in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    out.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
        except OSError as exc:
            log.warning("cost ledger read failed: %s", exc)
        return out

    def daily_totals(self, days: int = 14) -> list[dict]:
        """[{day, cost, requests, prompt_tokens, completion_tokens, cached_tokens, cache_hit_rate}]."""
        per_day: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"day": "", "cost": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cache_hit_rate": 0.0}
        )
        for line in self._read():
            try:
                day = datetime.fromisoformat(line["ts"]).astimezone().date().isoformat()
            except (KeyError, ValueError):
                continue
            agg = per_day[day]
            agg["day"] = day
            agg["cost"] += float(line.get("cost", 0.0))
            agg["requests"] += 1
            agg["prompt_tokens"] += int(line.get("prompt_tokens", 0))
            agg["completion_tokens"] += int(line.get("completion_tokens", 0))
            agg["cached_tokens"] += int(line.get("cached_tokens", 0))
            agg["cache_hit_rate"] = _cache_hit_rate(agg["prompt_tokens"], agg["cached_tokens"])
        return [per_day[d] for d in sorted(per_day, reverse=True)][:days]

    def weekly_totals(self, weeks: int = 4) -> list[dict]:
        """[{week, cost, requests, prompt_tokens, completion_tokens, cached_tokens, cache_hit_rate}]."""
        per_week: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"week": "", "cost": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cache_hit_rate": 0.0}
        )
        for line in self._read():
            try:
                dt = datetime.fromisoformat(line["ts"])
                week = f"{dt.isocalendar().year}-W{dt.isocalendar().week:02d}"
            except (KeyError, ValueError):
                continue
            agg = per_week[week]
            agg["week"] = week
            agg["cost"] += float(line.get("cost", 0.0))
            agg["requests"] += 1
            agg["prompt_tokens"] += int(line.get("prompt_tokens", 0))
            agg["completion_tokens"] += int(line.get("completion_tokens", 0))
            agg["cached_tokens"] += int(line.get("cached_tokens", 0))
            agg["cache_hit_rate"] = _cache_hit_rate(agg["prompt_tokens"], agg["cached_tokens"])
        return [per_week[w] for w in sorted(per_week, reverse=True)][:weeks]

    def totals(self) -> dict:
        rows = self._read()
        prompt = sum(int(r.get("prompt_tokens", 0)) for r in rows)
        cached = sum(int(r.get("cached_tokens", 0)) for r in rows)
        return {
            "requests": len(rows),
            "cost": round(sum(float(r.get("cost", 0.0)) for r in rows), 4),
            "prompt_tokens": prompt,
            "completion_tokens": sum(int(r.get("completion_tokens", 0)) for r in rows),
            "cached_tokens": cached,
            "cache_hit_rate": _cache_hit_rate(prompt, cached),
            # Honesty fields: a $0.00 total is only meaningful if every model
            # in it was either explicitly priced or explicitly free.
            "unpriced_models": unpriced_models(rows),
            "cost_is_measured": bool(rows) and not unpriced_models(rows),
        }
