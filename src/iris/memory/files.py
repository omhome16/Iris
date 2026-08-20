"""Workspace file manager — the source of truth.

Tiers live as plain Markdown files. This module owns:
- canonical paths for each tier
- reading files (with per-tier token budgets for bootstrap injection)
- appending daily notes (episodic, append-only)
- writing curated files with *optimistic concurrency*: the content hash is
  captured before a consolidation pass and re-checked immediately before an
  atomic rename; if another writer changed the file meanwhile, the write is
  aborted so no consolidation ever clobbers newer content.
"""

from __future__ import annotations

import hashlib
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from iris.config import settings
from iris.memory.chunking import estimate_tokens


class ConcurrencyError(RuntimeError):
    pass


class WorkspaceFiles:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "memory").mkdir(parents=True, exist_ok=True)
        (root / "skills").mkdir(parents=True, exist_ok=True)
        (root / "config").mkdir(parents=True, exist_ok=True)
        (root / ".dreams").mkdir(parents=True, exist_ok=True)

    # ── canonical paths ───────────────────────────────────────────────────
    @property
    def instructions(self) -> Path:
        return self.root / "AGENTS.md"

    @property
    def memory(self) -> Path:
        return self.root / "MEMORY.md"

    @property
    def user(self) -> Path:
        return self.root / "USER.md"

    @property
    def dreams(self) -> Path:
        return self.root / "DREAMS.md"

    def today(self) -> date:
        """The owner's *today* — daily notes, traces, and dreams must all be
        dated in the timezone gathered during onboarding, not the server's."""
        try:
            return datetime.now(ZoneInfo(settings.iris_timezone)).date()
        except Exception:  # noqa: BLE001 - bad tz config, fall back to UTC
            return datetime.now(ZoneInfo("UTC")).date()

    def daily_note(self, day: date | None = None) -> Path:
        return self.root / "memory" / f"{(day or self.today()).isoformat()}.md"

    def skills_dir(self) -> Path:
        return self.root / "skills"

    def staging_dir(self) -> Path:
        return self.root / ".dreams"

    def config_file(self) -> Path:
        return self.root / "config" / "iris.json"

    # ── reading ───────────────────────────────────────────────────────────
    def read(self, path: Path, *, max_tokens: int | None = None) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8")
        if max_tokens and estimate_tokens(text) > max_tokens:
            # Keep the TAIL: curated files are append-mostly, so the newest
            # facts live at the end. Truncating the head preserved stale
            # entries and silently dropped what changed most recently.
            tokens = text.split()
            text = " ".join(tokens[-max_tokens:]) + "\n\n[truncated: budget exceeded]"
        return text

    def bootstrap_memory(self) -> str:
        return self.read(self.memory, max_tokens=settings.bootstrap_budget_tokens)

    def bootstrap_user(self) -> str:
        return self.read(self.user, max_tokens=settings.user_profile_budget_tokens)

    def read_daily(self, day: date | None = None) -> str:
        return self.read(self.daily_note(day))

    # ── appending (episodic: never rewrite, only append) ──────────────────
    def append_daily(self, text: str, *, day: date | None = None, stamp: bool = True) -> None:
        path = self.daily_note(day)
        with path.open("a", encoding="utf-8") as fh:
            if stamp:
                fh.write(f"\n## {datetime.now(ZoneInfo(settings.iris_timezone)).isoformat(timespec='seconds')}\n")
            fh.write(text.rstrip() + "\n")

    def append_dreams(self, entry: str) -> None:
        with self.dreams.open("a", encoding="utf-8") as fh:
            fh.write(entry.rstrip() + "\n\n")

    # ── recall feedback (episodic: the agent went back to a memory) ──────
    def recall_feedback_path(self) -> Path:
        return self.root / ".dreams" / "recall_feedback.jsonl"

    def record_recall_feedback(self, path: str, content: str) -> None:
        """Log one recalled chunk. Rotates at max bytes (one generation kept).

        `content` is capped to a short snippet — enough for the Light phase
        to match staged signals against, without duplicating the note itself.
        """
        from datetime import datetime

        import json

        file = self.recall_feedback_path()
        max_bytes = settings.recall_feedback_max_bytes
        if file.exists() and file.stat().st_size >= max_bytes:
            old = file.with_suffix(".jsonl.1")
            if old.exists():
                old.unlink()
            file.rename(old)
        line = json.dumps(
            {
                "path": path[:200],
                "content": content[:400],
                "observed_at": datetime.now().isoformat(timespec="seconds"),
            },
            ensure_ascii=False,
        )
        with file.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def append_curated(self, path: Path, entry: str) -> None:
        """Append one entry to a curated file (e.g. explicit remember)."""
        with path.open("a", encoding="utf-8") as fh:
            fh.write("\n" + entry.rstrip() + "\n")

    # ── curated writes with optimistic concurrency ────────────────────────
    @staticmethod
    def _hash(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def write_curated(self, path: Path, new_text: str, *, expected_hash: str | None = None) -> None:
        """Atomically replace a curated file.

        If `expected_hash` is given and the file on disk no longer matches it,
        abort (ConcurrencyError) — another writer got there first.
        """
        if expected_hash is not None:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if self._hash(current) != expected_hash:
                raise ConcurrencyError("file changed since snapshot; write aborted")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(new_text, encoding="utf-8")
        os.replace(tmp, path)

    def snapshot_hash(self, path: Path) -> str:
        return self._hash(path.read_text(encoding="utf-8")) if path.exists() else ""

    # ── skills (procedural memory) ────────────────────────────────────────
    def skill_path(self, name: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in name).lower()
        return self.skills_dir() / f"{safe}.md"

    def list_skills(self) -> list[str]:
        return sorted(p.stem for p in self.skills_dir().glob("*.md"))

    def read_skill(self, name: str) -> str:
        return self.read(self.skill_path(name))