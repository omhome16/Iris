"""The skill registry — one roster built from every source skills can come from.

Sources, highest precedence first:

| Source | Where | Encoding |
|---|---|---|
| `learned` | `workspace/skills/<name>.json` + `.md` | flat sidecar (what Iris writes) |
| `workspace` | `workspace/skills/<name>/SKILL.md` | Agent Skills spec |
| `extra` | `settings.skills_extra_dirs` | spec |
| `package:<dist>` | distributions advertising the `iris_ai.skills` entry point | spec |
| `builtin` | `settings.skills_builtin_dir` (repo `skills/`) | spec |

Two design commitments, both about trust rather than features:

- **A name clash is data, not a coin flip.** The higher-precedence copy wins and
  every losing copy becomes a `RegistryConflict` naming both sources and the
  path — visible in `iris skills validate`.
- **No caching.** `list()` re-reads the sources on every call. At this roster size
  a scan is a handful of small files, and a stale roster is a much worse failure
  than a repeated glob: the writing side of this system is *dreaming*, which
  rewrites skills while the process runs.

The registry reads; `SkillLibrary` writes. The write methods here delegate (so
callers see one object) but only ever for **learned** skills — the encoding the
writing loop produces and maintains. Dreaming, `skill_write` and
reinforce/revise behave exactly as before.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.skills import Skill, SkillLibrary
from iris_ai.skills.manifest import (
    ManifestError,
    ValidationIssue,
    parse_sidecar,
    parse_skill_md,
    validate_skill,
)

log = logging.getLogger("iris_ai.skills")

# Lower number wins. Learned sits above workspace because a flat sidecar is the
# form the writing loop maintains; if both exist, the loop's copy is the current
# one and the directory copy is stale.
_PRECEDENCE = {"learned": 0, "workspace": 1, "extra": 2, "package": 3, "builtin": 4}

ENTRY_POINT_GROUP = "iris_ai.skills"


@dataclass(frozen=True, slots=True)
class RegistryConflict:
    name: str
    winner: str  # source that won
    loser: str  # source that was shadowed
    path: str  # the shadowed file

    def __str__(self) -> str:
        return f"{self.name!r}: {self.winner} wins over {self.loser} ({self.path})"


def _default_entry_points() -> list[tuple[str, Path]]:
    """Directories advertised by installed distributions.

    Any failure here is a missing roster, never a broken boot: a third-party
    package with a malformed entry point must not stop Iris from starting.
    """
    try:
        from importlib.metadata import entry_points

        groups = entry_points(group=ENTRY_POINT_GROUP)
    except Exception as exc:  # noqa: BLE001 - discovery is best-effort by design
        log.debug("skill entry points unavailable: %s", exc)
        return []
    found: list[tuple[str, Path]] = []
    for entry in groups:
        try:
            target = Path(str(entry.load()))
        except Exception as exc:  # noqa: BLE001 - same reasoning
            log.warning("skill entry point %s could not be loaded: %s", getattr(entry, "name", "?"), exc)
            continue
        found.append((entry.dist.name if entry.dist else entry.name or "dist", target))
    return found


def repo_root() -> Path:
    """The checkout the package is running from — where shipped skills live.

    Config-relative to the source tree, so a checkout finds `<root>/skills/` and
    an installed wheel simply has no builtins (discovery treats a missing root
    as an empty source, never an error).
    """
    return Path(__file__).resolve().parents[3]


def builtin_root() -> Path | None:
    """The configured builtin directory, resolved against the repository."""
    configured = settings.skills_builtin_dir.strip()
    if not configured:
        return None
    path = Path(configured)
    return path if path.is_absolute() else repo_root() / path


def extra_roots() -> list[Path]:
    """`SKILLS_EXTRA_DIRS` — comma-separated, relative to the cwd."""
    return [Path(part.strip()) for part in settings.skills_extra_dirs.split(",") if part.strip()]


def open_registry(
    files: WorkspaceFiles,
    *,
    known_tools: set[str] | None = None,
    enabled: bool | None = None,
    entry_points: Callable[[], list[tuple[str, Path]]] | None = None,
) -> SkillRegistry:
    """The registry *as configured*: the engine, the CLI and the tools all read
    the same roster from the same settings, so there is one place that decides
    where skills come from."""
    return SkillRegistry(
        files,
        builtin_dir=builtin_root(),
        extra_dirs=extra_roots(),
        known_tools=known_tools,
        enabled=enabled,
        entry_points=entry_points,
    )


class SkillRegistry:
    def __init__(
        self,
        files: WorkspaceFiles,
        *,
        builtin_dir: Path | str | None = None,
        extra_dirs: Sequence[Path | str] = (),
        entry_points: Callable[[], list[tuple[str, Path]]] | None = None,
        known_tools: set[str] | None = None,
        enabled: bool | None = None,
        library: SkillLibrary | None = None,
    ) -> None:
        self.files = files
        self.builtin_dir = Path(builtin_dir) if builtin_dir else None
        self.extra_dirs = [Path(p) for p in extra_dirs]
        self._entry_points = entry_points if entry_points is not None else _default_entry_points
        self.known_tools = known_tools
        self._enabled = enabled
        # Writes are delegated wholesale: dreaming, `skill_write` and the
        # reinforce/revise loop keep writing exactly where they always did.
        self.library = library if library is not None else SkillLibrary(files)

    # ── state ────────────────────────────────────────────────────────────
    @property
    def enabled(self) -> bool:
        return settings.skills_enabled if self._enabled is None else bool(self._enabled)

    # ── reading ──────────────────────────────────────────────────────────
    def list(self) -> list[Skill]:
        """Every discovered skill, enabled or not, in deterministic order."""
        skills, _issues, _conflicts = self._resolve()
        return skills

    def selectable(self) -> list[Skill]:
        """The roster the selector and the prompt may use: enabled and valid."""
        if not self.enabled:
            return []
        skills, issues, _conflicts = self._resolve()
        broken = {(i.name, i.source) for i in issues if i.level == "error" and i.name}
        return [s for s in skills if s.enabled and (s.name, s.source) not in broken]

    def get(self, name: str) -> Skill | None:
        return next((s for s in self.list() if s.name == name), None)

    async def suggest(self, message: str, *, jev=None):
        """Pick at most one skill for a turn (JEV when available).

        Kept on the registry so every caller gets the same roster and the same
        fallback: the deterministic matcher must never be a second code path
        that disagrees with the registry about what exists.
        """
        from iris_ai.jev import suggest_skill

        roster = self.selectable()
        if not roster:
            return None
        if jev is not None and getattr(jev, "enabled", False):
            suggestion = await suggest_skill(jev, message=message, skills=roster)
            if suggestion.screened:
                return suggestion
        return None

    def match_triggers(self, text: str) -> list[Skill]:
        """Skills whose trigger phrases appear in the text (casefolded)."""
        low = text.casefold()
        return [s for s in self.selectable() if any(t.casefold() in low for t in s.triggers)]

    # ── writing (delegated to SkillLibrary, with source rules) ───────────
    def _write_target(self, name: str) -> SkillLibrary:
        """Only the learned encoding is writable, and shipped skills never are.

        Two ways writing could go wrong, both silent before this check:
        reinforcing a *builtin* would create a workspace copy that shadows it
        (learned beats builtin in precedence) — a half-copy missing whatever the
        author wrote; and a hand-authored *directory* skill has no sidecar, so
        `SkillLibrary.reinforce` would quietly do nothing at all. Refusing names
        the reason instead.
        """
        existing = self.get(name)
        if existing is None:
            return self.library  # a write of a brand-new skill: learned by definition
        if existing.source != "learned":
            raise PermissionError(
                f"{name!r} is a {existing.source} skill (hand-authored or shipped): "
                "only learned skills carry a success score. Edit its SKILL.md, or write a "
                "learned skill to replace it."
            )
        return self.library

    def write(self, skill: Skill) -> None:
        self._write_target(skill.name).write(skill)

    def reinforce(self, name: str, *, delta: float = 0.1) -> Skill | None:
        return self._write_target(name).reinforce(name, delta=delta)

    def revise(self, name: str, *, delta: float = 0.1) -> Skill | None:
        return self._write_target(name).revise(name, delta=delta)

    # ── validation ───────────────────────────────────────────────────────
    def validate(self) -> list[ValidationIssue]:
        skills, issues, conflicts = self._resolve()
        out = list(issues)
        for skill in skills:
            out.extend(validate_skill(skill, known_tools=self.known_tools))
        for conflict in conflicts:
            out.append(
                ValidationIssue(
                    "warning",
                    f"shadowed by {conflict.winner}: {conflict.loser}",
                    name=conflict.name,
                    source=conflict.loser,
                    path=conflict.path,
                )
            )
        if not self.enabled:
            out.append(
                ValidationIssue(
                    "warning",
                    "skills are disabled (SKILLS_ENABLED=false): nothing will be selected or injected",
                )
            )
        return out

    @property
    def conflicts(self) -> list[RegistryConflict]:
        return self._resolve()[2]

    # ── discovery ────────────────────────────────────────────────────────
    def _resolve(self) -> tuple[list[Skill], list[ValidationIssue], list[RegistryConflict]]:
        """Scan every source, reconcile by precedence, order deterministically."""
        candidates: list[tuple[str, Skill, Path]] = []
        issues: list[ValidationIssue] = []

        def collect(paths: Iterable[tuple[Path, str]]) -> None:
            for path, source in paths:
                try:
                    parsed = parse_sidecar(
                        json.loads(path.read_text(encoding="utf-8")), source=source
                    ) if path.suffix == ".json" else parse_skill_md(
                        path.read_text(encoding="utf-8"), root=path.parent, source=source
                    )
                except ManifestError as exc:
                    issues.append(
                        ValidationIssue("error", str(exc), name=path.parent.name if path.name == "SKILL.md" else path.stem, source=source, path=str(path))
                    )
                    continue
                except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                    issues.append(
                        ValidationIssue("error", f"could not be read: {exc}", name=path.stem, source=source, path=str(path))
                    )
                    continue
                candidates.append((source, parsed.skill, path))
                issues.extend(parsed.issues)

        collect(self._workspace_sidecars())
        collect(self._skill_directories(self.files.skills_dir(), "workspace"))
        for directory in self.extra_dirs:
            collect(self._skill_directories(directory, "extra"))
        for dist, directory in self._entry_points():
            collect(self._skill_directories(directory, f"package:{dist}"))
        if self.builtin_dir is not None:
            collect(self._skill_directories(self.builtin_dir, "builtin"))

        # Precedence resolution: keep the best copy of each name, record the rest.
        ordered = sorted(
            candidates,
            key=lambda item: (_PRECEDENCE.get(item[0].split(":")[0], 99), item[0], item[1].name),
        )
        kept: dict[str, Skill] = {}
        conflicts: list[RegistryConflict] = []
        for source, skill, path in ordered:
            if skill.name in kept:
                conflicts.append(
                    RegistryConflict(
                        name=skill.name,
                        winner=kept[skill.name].source,
                        loser=source,
                        path=str(path),
                    )
                )
                continue
            kept[skill.name] = skill

        # Deterministic presentation order: source precedence, then name.
        skills = sorted(
            kept.values(),
            key=lambda s: (_PRECEDENCE.get(s.source.split(":")[0], 99), s.source, s.name),
        )
        return skills, issues, conflicts

    def _workspace_sidecars(self) -> list[tuple[Path, str]]:
        directory = self.files.skills_dir()
        if not directory.is_dir():
            return []
        return [(p, "learned") for p in sorted(directory.glob("*.json"))]
    # NOTE: nothing below writes. SkillLibrary is the only writer.

    @staticmethod
    def _skill_directories(root: Path, source: str) -> list[tuple[Path, str]]:
        """`<root>/<name>/SKILL.md` for every child directory that has one."""
        if not root or not Path(root).is_dir():
            return []
        return [
            (child / "SKILL.md", source)
            for child in sorted(Path(root).iterdir())
            if child.is_dir() and (child / "SKILL.md").is_file()
        ]
