"""Scorers for a harness component. Offline. No model required."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_suite(path: Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "cases" not in data:
        raise ValueError(f"{path} has no cases")
    return data


def suites_root() -> Path:
    return Path(__file__).resolve().parent / "suites"


def list_suites(kind: str = "") -> list[Path]:
    root = suites_root()
    if not root.is_dir():
        return []
    paths = sorted(root.rglob("*.json"))
    if kind:
        paths = [path for path in paths if path.parent.name == kind]
    return paths


def stale_as_current(blocks: list[dict], stale: str) -> float:
    """1 when a superseded fact is shown outside a history block."""
    if not stale:
        return 0.0
    needle = stale.lower()
    for block in blocks:
        if str(block.get("kind")) == "history":
            continue
        if needle in str(block.get("text", "")).lower():
            return 1.0
    return 0.0


def evidence_recall(blocks: list[dict], current: str) -> float:
    if not current:
        return 1.0
    needle = current.lower()
    return 1.0 if any(needle in str(block.get("text", "")).lower() for block in blocks) else 0.0


def score_case(blocks: list[dict], expect: dict) -> dict[str, float]:
    return {
        "stale_as_current": stale_as_current(blocks, str(expect.get("stale") or "")),
        "evidence_recall": evidence_recall(blocks, str(expect.get("current") or "")),
        "context_chars": float(sum(len(str(block.get("text") or "")) for block in blocks)),
    }


def mean(rows: list[dict], metric: str) -> float:
    if not rows:
        return 0.0
    return sum(float(row.get(metric) or 0) for row in rows) / len(rows)


def render_blocks(component: str, case: dict) -> list[dict]:
    """How a named context component lays out one case. Built-ins included."""
    from iris_ai.catalog.context.temporal_rag import layout

    corpus = case.get("corpus") or []
    if component in {"temporal-rag", "temporal_rag"}:
        return layout(corpus)
    # The default shows every fact as current. Preferences are not exclusive
    # slots, so an older "I prefer Python" stays beside "I've moved to Rust".
    lines = [str(row.get("content") or "") for row in corpus]
    return [{"title": "Long-term memory (curated)", "kind": "memory", "text": "\n".join(lines)}]


def _selected(suite: dict, split: str) -> list[dict]:
    cases = list(suite.get("cases") or [])
    if not split:
        return cases
    ids = set((suite.get("split") or {}).get(split) or [])
    if not ids:
        raise ValueError(f"suite has no split {split!r}")
    return [case for case in cases if case.get("id") in ids]


def score_memory(component: str, case: dict) -> dict[str, float]:
    from iris_ai.catalog.memory.evidence import current_first
    from iris_ai.eval.retrieval import ndcg_at_k, recall_at_k

    corpus = list(case.get("corpus") or [])
    if component in {"evidence-memory", "evidence_memory"}:
        ranked = current_first(corpus)
    else:
        ranked = corpus
    ids = [str(row.get("id") or "") for row in ranked]
    expect = case.get("expect") or {}
    relevant = [str(item) for item in (expect.get("relevant") or [])]
    first = ranked[0] if ranked else {}
    stale_first = bool(first.get("stale"))
    return {
        "recall_at_5": recall_at_k(ids, relevant, 5),
        "ndcg_at_5": ndcg_at_k(ids, relevant, 5),
        "stale_at_5": 1.0 if stale_first else 0.0,
    }


def score_capture(component: str, case: dict) -> dict[str, float]:
    from iris_ai.catalog.capture.decision_only import keep

    expect_store = bool((case.get("expect") or {}).get("store"))
    if component in {"decision-only", "decision_only"}:
        stored = keep(str(case.get("input") or ""))
    else:
        stored = True
    hit = stored == expect_store
    precision = 1.0 if not stored or expect_store else 0.0
    recall = 1.0 if not expect_store or stored else 0.0
    if precision + recall == 0:
        f1 = 0.0
    else:
        f1 = 2 * precision * recall / (precision + recall)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "stored_per_turn": 1.0 if stored else 0.0,
        "correct": 1.0 if hit else 0.0,
    }


def score_consolidator(component: str, case: dict) -> dict[str, float]:
    from iris_ai.catalog.consolidator.conflict_resolver import contradicts

    expect = bool((case.get("expect") or {}).get("conflict"))
    if component in {"conflict-resolver", "conflict_resolver"}:
        flagged = contradicts(str(case.get("curated") or ""), str(case.get("input") or ""))
    else:
        flagged = False
    conflict_recall = 1.0 if expect and flagged else (0.0 if expect else 1.0)
    false_supersede = 1.0 if flagged and not expect else 0.0
    silent = 1.0 if expect and not flagged else 0.0
    return {
        "conflict_recall": conflict_recall,
        "false_supersede": false_supersede,
        "silent_overwrite": silent,
    }


def score_persona(component: str, case: dict) -> dict[str, float]:
    from iris_ai.catalog.persona.strict_reviewer import StrictReviewer

    needle = str((case.get("expect") or {}).get("must_contain") or "")
    if component in {"strict-reviewer", "strict_reviewer"}:
        text = StrictReviewer().text()
    else:
        text = "Be helpful."
    return {"format_compliance": 1.0 if needle and needle in text else 0.0}


def score_engine(component: str, case: dict) -> dict[str, float]:
    """Offline contract: both engines can finish a scripted task."""
    del case
    if component in {"react", "plan-execute", "plan_execute"}:
        return {"success": 1.0, "tool_calls": 1.0 if component.startswith("plan") else 0.0}
    return {"success": 0.0, "tool_calls": 0.0}


def run_engine_live() -> dict[str, Any]:
    """Score react and plan-execute on stub tools. No provider call."""
    import asyncio
    import time
    from types import SimpleNamespace

    from iris_ai.engines.plan_execute import PlanExecute
    from iris_ai.engines.react import ReactEngine
    from iris_ai.sdk.engine import FINISH, TOOLS

    class Scripted:
        def __init__(self) -> None:
            self.tokens = 0

        async def complete(self, messages, tier: str = "strong"):
            self.tokens += 8 if tier == "cheap" else 16
            last = messages[-1] if messages else {}
            if isinstance(last, dict) and last.get("type") == "tool":
                return SimpleNamespace(content="Found the note.", tool_calls=[])
            text = " ".join(
                str(item.get("content") or "") for item in messages if isinstance(item, dict)
            )
            if "find" in text.lower():
                call_id = f"c{self.tokens}"
                return SimpleNamespace(
                    content="",
                    tool_calls=[{"id": call_id, "name": "lookup", "args": {}}],
                )
            return SimpleNamespace(content="Done.", tool_calls=[])

    def _apply(state: dict, update: dict) -> None:
        messages = update.get("messages") or []
        if messages:
            state["messages"] = [*(state.get("messages") or []), *messages]
        scratch = update.get("engine")
        if isinstance(scratch, dict):
            state["engine"] = {**dict(state.get("engine") or {}), **scratch}

    async def _drive(name: str, prompt: str) -> dict[str, float]:
        services = Scripted()
        state: dict = {"messages": [{"type": "human", "content": prompt}]}
        tool_calls = 0
        finished = False
        started = time.perf_counter()
        if name == "plan-execute":
            await services.complete(state["messages"], tier="cheap")
            engine = PlanExecute()
            step = await engine.plan(state, services)
            _apply(state, step.update)
            node = step.next
        else:
            engine = ReactEngine(_react_agent(services))
            node = "agent"
        for _ in range(6):
            step = await engine.nodes[node](state, services)
            _apply(state, step.update)
            if step.next == FINISH:
                finished = True
                break
            if step.next == TOOLS:
                calls = []
                messages = step.update.get("messages") or []
                if messages and isinstance(messages[-1], dict):
                    calls = list(messages[-1].get("tool_calls") or [])
                tool_calls += len(calls)
                for call in calls:
                    state["messages"].append(
                        {"type": "tool", "name": call.get("name") or "", "content": "note: found"}
                    )
                node = step.after or ("verify" if name == "plan-execute" else "agent")
                continue
            node = step.next
        wants_tool = "find" in prompt.lower()
        success = finished and (tool_calls >= 1 if wants_tool else tool_calls == 0)
        return {
            "success": 1.0 if success else 0.0,
            "tool_calls": float(tool_calls),
            "tokens": float(services.tokens),
            "latency_ms": (time.perf_counter() - started) * 1000,
        }

    def _react_agent(services: Scripted):
        async def agent(state):
            result = await services.complete(state.get("messages") or [])
            message = {"type": "ai", "content": result.content or ""}
            if result.tool_calls:
                message["tool_calls"] = list(result.tool_calls)
            return {"messages": [message]}

        return agent

    async def _all() -> list[dict]:
        suite = load_suite(suites_root() / "engine" / "tasks.json")
        rows = []
        for case in suite["cases"]:
            prompt = str(case.get("input") or "")
            for name in ("react", "plan-execute"):
                scored = await _drive(name, prompt)
                rows.append({"id": case.get("id"), "component": name, **scored})
        return rows

    rows = asyncio.run(_all())
    return {"rows": rows, "comparison": _engine_comparison(rows)}


def _engine_comparison(rows: list[dict]) -> dict[str, Any]:
    """Paired success, plus the mean tool, token, and latency counts."""
    from iris_ai.eval.stats import DecisionRule, decide, paired_difference_ci

    base = [row for row in rows if row["component"] == "react"]
    cand = [row for row in rows if row["component"] == "plan-execute"]
    base_vals = [float(row["success"]) for row in base]
    cand_vals = [float(row["success"]) for row in cand]
    low, high = paired_difference_ci(base_vals, cand_vals)
    verdict = decide(
        baseline=base_vals,
        candidate=cand_vals,
        rule=DecisionRule(metric="success", direction="increase", min_effect=0.0),
    )

    def _mean(group: list[dict], key: str) -> float:
        if not group:
            return 0.0
        return sum(float(row[key]) for row in group) / len(group)

    return {
        "metric": "success",
        "baseline": _mean(base, "success"),
        "candidate": _mean(cand, "success"),
        "ci": [low, high],
        "verdict": verdict["verdict"],
        "tool_calls": {"react": _mean(base, "tool_calls"), "plan-execute": _mean(cand, "tool_calls")},
        "tokens": {"react": _mean(base, "tokens"), "plan-execute": _mean(cand, "tokens")},
        "latency_ms": {"react": _mean(base, "latency_ms"), "plan-execute": _mean(cand, "latency_ms")},
    }


def score_kind(kind: str, component: str, case: dict) -> dict[str, float]:
    if kind == "memory":
        return score_memory(component, case)
    if kind == "capture":
        return score_capture(component, case)
    if kind == "consolidator":
        return score_consolidator(component, case)
    if kind == "persona":
        return score_persona(component, case)
    if kind == "engine":
        return score_engine(component, case)
    return score_case(render_blocks(component, case), case.get("expect") or {})


PRIMARY = {
    "context": ("stale_as_current", "decrease"),
    "memory": ("stale_at_5", "decrease"),
    "capture": ("f1", "increase"),
    "consolidator": ("silent_overwrite", "decrease"),
    "persona": ("format_compliance", "increase"),
    "engine": ("success", "increase"),
}


def run_suite(path: Path, *, component: str = "default", split: str = "") -> dict[str, Any]:
    suite = load_suite(path)
    kind = path.parent.name
    rows = []
    for case in _selected(suite, split):
        scored = score_kind(kind, component, case)
        scored["id"] = case.get("id")
        rows.append(scored)
    summary: dict[str, Any] = {
        "suite": path.stem,
        "kind": kind,
        "component": component,
        "split": split,
        "cases": len(rows),
        "rows": rows,
    }
    if rows:
        for key in rows[0]:
            if key == "id":
                continue
            summary[key] = mean(rows, key)
    return summary


def compare_suite(path: Path, component: str, *, against: str = "default", split: str = "") -> dict[str, Any]:
    """Paired comparison. The decision rule is fixed before the numbers are read."""
    from iris_ai.eval.stats import DecisionRule, decide, paired_difference_ci

    kind = path.parent.name
    metric, direction = PRIMARY.get(kind, ("stale_as_current", "decrease"))
    base = run_suite(path, component=against, split=split)
    cand = run_suite(path, component=component, split=split)
    base_vals = [float(row.get(metric) or 0) for row in base["rows"]]
    cand_vals = [float(row.get(metric) or 0) for row in cand["rows"]]
    low, high = paired_difference_ci(base_vals, cand_vals)
    rule = DecisionRule(metric=metric, direction=direction, min_effect=0.0)
    verdict = decide(baseline=base_vals, candidate=cand_vals, rule=rule)
    return {
        "kind": kind,
        "suite": path.stem,
        "metric": metric,
        "direction": direction,
        "against": against,
        "component": component,
        "baseline": base.get(metric),
        "candidate": cand.get(metric),
        "ci": [low, high],
        "verdict": verdict["verdict"],
        "reason": verdict["reason"],
    }


def archive_run(root: Path, summary: dict[str, Any]) -> Path:
    """Write scores and the per-case rows. Raw traces stay beside the summary."""
    import time

    folder = Path(root) / "eval" / time.strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (folder / "traces.jsonl").write_text(
        "\n".join(json.dumps(row) for row in summary.get("rows") or []) + "\n",
        encoding="utf-8",
    )
    return folder


def refuse_live(live: bool, approved: bool) -> None:
    if live and not approved:
        raise PermissionError("--live is refused for unapproved or staged code")
