"""Cost ledger — append-only JSONL of LLM usage, with daily/weekly rollups.

Every provider call (complete / complete_with_tools / embed) records its
usage line to `workspace/config/llm_calls.jsonl` when a ledger is attached
to the LLMClient. Prices are per-1M-token estimates per model; unknown
models fall back to zero (never fail a call because of accounting).
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("iris.ledger")

# USD per 1M tokens, keyed by model name; "" matches any unknown → $0.
PRICE_PER_1M: dict[str, tuple[float, float]] = {
    "gemini/gemini-2.5-flash": (0.30, 2.50),
    "gemini/gemini-2.5-flash-preview-08-17": (0.30, 2.50),
    "gemini/gemini-2.0-flash": (0.10, 0.40),
    "gemini/text-embedding-004": (0.0, 0.0),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    in_price, out_price = PRICE_PER_1M.get(model, (0.0, 0.0))
    return prompt_tokens * in_price / 1_000_000 + completion_tokens * out_price / 1_000_000


class CostLedger:
    """Append-only JSONL store at workspace/config/llm_calls.jsonl."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, *, model: str, tier: str, prompt_tokens: int, completion_tokens: int) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "model": model,
                "tier": tier,
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
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
        """[{day: 'YYYY-MM-DD', cost, requests, prompt_tokens, completion_tokens}]."""
        per_day: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"day": "", "cost": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
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
        return [per_day[d] for d in sorted(per_day, reverse=True)][:days]

    def weekly_totals(self, weeks: int = 4) -> list[dict]:
        """[{week: 'YYYY-Www', cost, requests, prompt_tokens, completion_tokens}]."""
        per_week: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"week": "", "cost": 0.0, "requests": 0, "prompt_tokens": 0, "completion_tokens": 0}
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
        return [per_week[w] for w in sorted(per_week, reverse=True)][:weeks]

    def totals(self) -> dict:
        rows = self._read()
        return {
            "requests": len(rows),
            "cost": round(sum(float(r.get("cost", 0.0)) for r in rows), 4),
            "prompt_tokens": sum(int(r.get("prompt_tokens", 0)) for r in rows),
            "completion_tokens": sum(int(r.get("completion_tokens", 0)) for r in rows),
        }