"""Skills — procedural memory.

Iris learns by writing her own reusable procedures. Each skill is a Markdown
file (`skills/<name>.md`) with a JSON metadata sidecar (`skills/<name>.json`):
name, description, trigger phrases, timestamps, and a coarse "success score"
updated on repeat use. Skills are indexed like curated memory (evergreen) so
they can be found by the agent at trigger time.

Lifecycle: agent proposes a skill (during dreaming or on demand) → written to
procedural tier → invoked via `skill_apply` when triggers match → reinforced
(success score up) or revised when it fails.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from iris.memory.files import WorkspaceFiles


@dataclass(slots=True)
class Skill:
    name: str
    description: str
    triggers: list[str] = field(default_factory=list)
    procedure: str = ""
    success_score: float = 0.5  # 0..1, updated on apply outcomes
    created: str = ""
    updated: str = ""

    def to_markdown(self) -> str:
        created = self.created or datetime.now().isoformat(timespec="seconds")
        updated = self.updated or created
        return (
            f"# Skill: {self.name}\n\n"
            f"> {self.description}\n\n"
            f"- Triggers: {', '.join(self.triggers) or '(none)'}\n"
            f"- Created: {created} · Updated: {updated}\n"
            f"- Success score: {self.success_score:.2f}\n\n"
            f"## Procedure\n\n{self.procedure.strip()}\n"
        )


class SkillLibrary:
    def __init__(self, files: WorkspaceFiles) -> None:
        self.files = files
        self.dir = files.skills_dir()
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, skill: Skill) -> None:
        """Persist skill markdown + JSON sidecar (atomic replace)."""
        now = datetime.now().isoformat(timespec="seconds")
        skill.updated = now
        if not skill.created:
            skill.created = now
        md_path = self.files.skill_path(skill.name)
        meta_path = md_path.with_suffix(".json")
        md_path.write_text(skill.to_markdown(), encoding="utf-8")
        meta_path.write_text(json.dumps(asdict(skill), indent=2) + "\n", encoding="utf-8")

    def get(self, name: str) -> Skill | None:
        meta = self.files.skill_path(name).with_suffix(".json")
        if not meta.exists():
            return None
        data = json.loads(meta.read_text(encoding="utf-8"))
        return Skill(**data)

    def list(self) -> list[Skill]:
        out = []
        for meta in sorted(self.dir.glob("*.json")):
            try:
                data = json.loads(meta.read_text(encoding="utf-8"))
                out.append(Skill(**data))
            except (json.JSONDecodeError, TypeError):
                continue
        return out

    def delete(self, name: str) -> bool:
        stem = self.files.skill_path(name).stem
        removed = False
        for p in self.dir.glob(f"{stem}.*"):
            p.unlink()
            removed = True
        return removed

    def reinforce(self, name: str, *, delta: float = 0.1) -> Skill | None:
        """Bump success score after a successful apply (cap 1.0)."""
        skill = self.get(name)
        if skill is None:
            return None
        skill.success_score = min(1.0, skill.success_score + delta)
        self.write(skill)
        return skill

    def match_triggers(self, text: str) -> list[Skill]:
        """Skills whose trigger phrases appear in the inbound text (casefold)."""
        low = text.casefold()
        return [
            s for s in self.list()
            if any(t.casefold() in low for t in s.triggers)
        ]