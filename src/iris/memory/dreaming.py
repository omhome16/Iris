"""Dreaming — the sleep graph: Light → REM → Deep → DREAMS.md.

Deterministic-first design (OpenClaw lineage):
- LIGHT: no model calls. Dedupe staged signals, score them by weighted
  signals (occurrence/recurrence, importance, conceptual richness, trigger
  diversity) and run a deterministic promotion gate. Tainted origins
  (UNTRUSTED/SYSTEM) are structurally excluded — they can never promote.
- REM: cheap-model theme reflections over promoted signals. Consolidated
  statements with evidence anchors (Generative Agents pattern). Falls back
  to one-theme-per-signal if the model is unavailable.
- DEEP: consolidation rewrite of MEMORY.md — superseded entries retired by
  key, new statements appended with anchors, optimistic concurrency
  (content-hash re-check before atomic rename; append-only fallback),
  pre-image archived, human-readable dream record appended to DREAMS.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from iris.config import settings
from iris.memory.files import ConcurrencyError, WorkspaceFiles
from iris.memory.index import MemoryIndex
from iris.memory.llm import LLMClient
from iris.memory.provenance import Origin, Provenance

_REM_SYSTEM = """You are the REM phase of Iris's dreaming.

Group the promoted memory signals below into coherent themes. For each theme
write ONE consolidated statement: durable, compact, self-contained, present
tense, with dates where relevant. Cite evidence by index (list the signal
indices that support the statement).

Respond ONLY with JSON: {"themes": [{"theme": "<short name>",
"statement": "<consolidated>", "evidence": [0, 2]}]}"""


@dataclass(slots=True)
class StagedSignal:
    op: str
    content: str
    importance: float
    triggers: list[str]
    target: str
    provenance: Provenance
    occurrences: int = 1
    recall_hits: int = 0

    @property
    def promotable(self) -> bool:
        return self.provenance.origin in (Origin.OWNER, Origin.AGENT)


@dataclass(slots=True)
class DreamTheme:
    theme: str
    statement: str
    evidence: list[int]
    importance: float


@dataclass(slots=True)
class DreamRecord:
    timestamp: str
    staged: int
    promoted: int
    themes: list[DreamTheme]
    added: int
    superseded: int
    fallback: bool = False

    def to_markdown(self) -> str:
        lines = [
            f"## Dream {self.timestamp}",
            f"- staged={self.staged} promoted={self.promoted} "
            f"themes={len(self.themes)} added={self.added} superseded={self.superseded}"
            + (" (append fallback: concurrent write detected)" if self.fallback else ""),
        ]
        for t in self.themes:
            lines.append(f"- [{t.importance:.0f}] {t.statement}")
        return "\n".join(lines)


class LightPhase:
    """Deterministic scoring + promotion gate. No model calls."""

    def score(self, signal: StagedSignal) -> float:
        occ = min(1.0, signal.occurrences / 3.0)
        imp = signal.importance / 10.0
        richness = min(1.0, len(signal.content) / 200.0)
        triggers = min(1.0, len(signal.triggers) / 4.0)
        recall = min(1.0, signal.recall_hits / 3.0)
        w = settings.dream_light_weights
        return w[0] * occ + w[1] * imp + w[2] * richness + w[3] * triggers + w[4] * recall

    @staticmethod
    def _recall_counts(feedback_file: Path | None) -> list[str]:
        """Normalized content snippets of recalled chunks (best-effort)."""
        if feedback_file is None or not feedback_file.exists():
            return []
        try:
            lines = feedback_file.read_text(encoding="utf-8").splitlines()
        except OSError:  # noqa: BLE001 - feedback is best-effort
            return []
        return [str(json.loads(line).get("content", "")).casefold() for line in lines if line.strip()]

    def run(self, staging_dir: Path, feedback_file: Path | None = None) -> tuple[list[StagedSignal], int]:
        """Read staging files, dedupe by normalized content, gate promotion.

        Returns (promoted, staged_count). Demoted signals stay in staging
        files untouched — they get re-scored at the next sleep.
        """
        by_content: dict[str, StagedSignal] = {}
        for path in sorted(staging_dir.glob("staging-*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                raw = json.loads(line)
                prov = raw.get("provenance", {})
                sig = StagedSignal(
                    op=str(raw.get("op", "ADD")),
                    content=str(raw.get("content", "")).strip(),
                    importance=float(raw.get("importance", 0.0)),
                    triggers=list(raw.get("triggers", []))[:5],
                    target=str(raw.get("target", "")),
                    provenance=Provenance(
                        origin=Origin(prov.get("origin", "untrusted")),
                        source=prov.get("source", "staging"),
                    ),
                )
                if not sig.content:
                    continue
                key = sig.content.casefold().strip()
                existing = by_content.get(key)
                if existing:
                    existing.occurrences += 1
                    if sig.importance > existing.importance:
                        existing.importance = sig.importance
                else:
                    by_content[key] = sig

        recalled = self._recall_counts(feedback_file)
        for sig in by_content.values():
            if recalled:
                sig.recall_hits = min(
                    3,
                    sum(1 for rc in recalled if sig.content.casefold() in rc),
                )

        promoted = [
            s for s in by_content.values()
            if s.promotable and self._gate(s)
        ]
        return promoted, len(by_content)

    def _gate(self, signal: StagedSignal) -> bool:
        if signal.provenance.origin is Origin.OWNER:
            return True  # explicit owner statements always promote
        return self.score(signal) >= settings.dream_gate_score or (
            signal.importance >= settings.dream_gate_importance
        )


class RemPhase:
    """Theme reflections over promoted signals (cheap model, JSON mode)."""

    def __init__(self, llm: LLMClient) -> None:
        self.llm = llm

    async def run(self, signals: list[StagedSignal]) -> list[DreamTheme]:
        if not signals:
            return []
        idx = {id(s): i for i, s in enumerate(signals)}
        payload = await self.llm.complete(
            [
                {"role": "system", "content": _REM_SYSTEM},
                {
                    "role": "user",
                    "content": "\n".join(
                        f"[{i}] (imp {s.importance:.0f}) {s.content}"
                        for i, s in enumerate(signals)
                    ),
                },
            ],
            tier="cheap",
            json_mode=True,
            max_tokens=1000,
        )
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return self._fallback(signals)
        themes: list[DreamTheme] = []
        for item in data.get("themes", []):
            statement = str(item.get("statement", "")).strip()
            if not statement:
                continue
            evidence = [int(i) for i in item.get("evidence", []) if 0 <= int(i) < len(signals)]
            imp = max((signals[i].importance for i in evidence), default=5.0)
            themes.append(DreamTheme(theme=str(item.get("theme", "general")), statement=statement, evidence=evidence, importance=imp))
        return themes or self._fallback(signals)

    @staticmethod
    def _fallback(signals: list[StagedSignal]) -> list[DreamTheme]:
        return [
            DreamTheme(theme="signal", statement=s.content, evidence=[i], importance=s.importance)
            for i, s in enumerate(signals)
        ]


class DeepPhase:
    """Consolidation rewrite of MEMORY.md with optimistic concurrency."""

    def __init__(self, files: WorkspaceFiles, index: MemoryIndex | None = None) -> None:
        self.files = files
        self.index = index

    async def run(self, themes: list[DreamTheme], signals: list[StagedSignal]) -> DreamRecord:
        record = DreamRecord(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            staged=0,
            promoted=len(signals),
            themes=themes,
            added=0,
            superseded=0,
        )
        if not themes:
            return record

        memory = self.files.memory
        snapshot = self.files.snapshot_hash(memory)
        current = self.files.read(memory) if memory.exists() else ""

        # pre-image archive
        pre_dir = self.files.root / ".dreams" / "preimages"
        pre_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        (pre_dir / f"{stamp}-MEMORY.md").write_text(current, encoding="utf-8")

        # drop the "not yet born" placeholder once there is real content
        if "\n\n_Empty" in current:
            current = current.split("\n\n_Empty", 1)[0]
        lines = current.splitlines()

        # supersession: retire entries whose key appears in a signal's target
        targets = {s.target.casefold() for s in signals if s.target}
        retired: list[int] = []
        for i, line in enumerate(lines):
            low = line.casefold()
            if any(t and t in low for t in targets):
                if "(superseded" not in low:
                    lines[i] = f"{line} (superseded {record.timestamp[:10]})"
                    retired.append(i)

        # new consolidated statements, deduped against what MEMORY.md already
        # says (cosine via the index; deterministic, no model call)
        anchor = f"memory/{datetime.now().strftime('%Y-%m-%d')}.md"
        existing = "\n".join(lines)
        additions: list[str] = []
        for t in themes:
            if self.index is not None and await self._duplicates(t.statement, existing):
                continue
            additions.append(f"- [{t.importance:.0f}] {t.statement} (from: {anchor})")

        body = "\n".join([*lines, *additions]) if lines else "\n".join(additions)
        if not body.startswith("# MEMORY.md"):
            body = f"# MEMORY.md — Iris long-term memory\n\n{body}"

        record.added = len(additions)
        record.superseded = len(retired)
        try:
            self.files.write_curated(memory, body, expected_hash=snapshot)
        except ConcurrencyError:
            # append-only fallback: never clobber newer content
            with memory.open("a", encoding="utf-8") as fh:
                fh.write("\n" + "\n".join(additions) + "\n")
            record.fallback = True

        self.files.append_dreams(record.to_markdown())
        return record

    async def _duplicates(self, statement: str, existing: str) -> bool:
        """True if MEMORY.md already says essentially the same thing."""
        if not existing.strip():
            return False
        try:
            near = await self.index.nearest(statement, top_k=3)
        except Exception:  # noqa: BLE001 - dedupe is best-effort
            return False
        for hit in near:
            if hit["path"] == "MEMORY.md" and hit["cos"] >= 0.85:
                return True
        return False


class DreamEngine:
    """Sleep orchestration: Light → REM → Deep."""

    def __init__(self, llm: LLMClient, files: WorkspaceFiles, index: MemoryIndex | None = None) -> None:
        self.light = LightPhase()
        self.rem = RemPhase(llm)
        self.deep = DeepPhase(files, index)
        self.files = files

    async def sleep(self) -> DreamRecord:
        promoted, staged = self.light.run(
            self.files.staging_dir(),
            feedback_file=self.files.recall_feedback_path(),
        )
        themes = await self.rem.run(promoted)
        record = await self.deep.run(themes, promoted)
        record.staged = staged
        if promoted:
            self._consume(promoted)
        return record

    def _consume(self, promoted: list[StagedSignal]) -> None:
        """Remove promoted (now consolidated) signals from staging files so
        the next sleep does not re-promote duplicates. Demoted signals stay."""
        dropped = {s.content.casefold().strip() for s in promoted}
        for path in self.files.staging_dir().glob("staging-*.jsonl"):
            remaining = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.strip()
                or json.loads(line).get("content", "").casefold().strip() not in dropped
            ]
            if remaining:
                path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
            else:
                path.unlink()