"""The skill registry — one roster built from every source skills can come from.

Sources, highest precedence first:

| Source | Where | Encoding |
|---|---|---|
| `learned` | `workspace/skills/<name>.json` + `.md` | flat sidecar (what Iris writes) |
| `workspace` | `workspace/skills/<name>/SKILL.md` | Agent Skills spec |
| `extra` | `settings.skills_extra_dirs` | spec |
| `package:<dist>` | distributions advertising the `iris_ai.skills` entry point | spec |
| `builtin` | `settings.skills_builtin_dir` (repo `skills/`) | spec |

Three design commitments, all about trust rather than features:

- **A name clash is data, not a coin flip.** The higher-precedence copy wins and
  every losing copy becomes a `RegistryConflict` naming both sources and the
  path — visible in `iris skills validate`.
- **A third-party manifest is hash-pinned.** Sources this workspace does not own
  (`package:`, `extra`) are approved by digest, and a *changed* digest disables
  the skill until `iris skills approve <name>` re-pins it. A trusted tool that
  quietly changes under you is the rug pull the vault warns about; see
  `iris_ai/skills/approvals.py`.
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
from iris_ai.skills.approvals import (
    ApprovalRecord,
    SkillApprovalStore,
    approval_key,
    is_pinned,
)
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


def packaged_builtin_root() -> Path | None:
    """Builtins shipped *inside* the wheel.

    In a source checkout the builtins are `<repo>/skills/`; an installed wheel
    has no repo root, so hatch force-includes the same directory as
    `iris_ai/builtin_skills`. Without this the shipped skill silently vanished
    on `pip install` while the README still advertised it.
    """
    packaged = Path(__file__).resolve().parent.parent / "builtin_skills"
    return packaged if packaged.is_dir() else None


def builtin_root() -> Path | None:
    """The configured builtin directory: the checkout's, else the packaged copy.

    `skills_builtin_dir = ""` still disables the source entirely, which is why
    the configured value is checked before the fallback.
    """
    configured = settings.skills_builtin_dir.strip()
    if not configured:
        return None
    path = Path(configured)
    resolved = path if path.is_absolute() else repo_root() / path
    if resolved.is_dir():
        return resolved
    return packaged_builtin_root()


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
        approvals: SkillApprovalStore | None = None,
    ) -> None:
        self.files = files
        self.builtin_dir = Path(builtin_dir) if builtin_dir else None
        self.extra_dirs = [Path(p) for p in extra_dirs]
        self._entry_points = entry_points if entry_points is not None else _default_entry_points
        self.known_tools = known_tools
        self._enabled = enabled
        # Trust state for third-party manifests, derived from the workspace so
        # every construction site (engine, CLI, tests) agrees without passing it.
        self.approvals = approvals if approvals is not None else SkillApprovalStore(files.skill_approvals_path())
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
        """The roster the selector and the prompt may use: enabled and valid.

        Error-level issues are keyed `(name, source)`, which is exactly how a
        failed approval pin removes a skill here — the same mechanism that
        already drops a manifest naming a tool that does not exist.
        """
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

    # ── approvals (third-party manifests) ────────────────────────────────
    def approve(self, name: str) -> ApprovalRecord | None:
        """Re-pin a third-party skill's current manifest.

        The remedy for a `changed` digest: the owner has looked at the new
        content and accepts it. Returns None when the skill is unknown or comes
        from a source this workspace owns — owner-authored and learned skills
        are not pinned, so there is nothing to re-approve. Raises
        `ApprovalLedgerError` when the ledger itself is unreadable, because the
        fix for that is repairing the file, not pinning on top of it.
        """
        skill = self.get(name)
        if skill is None or not is_pinned(skill.source):
            return None
        if not skill.root:
            return None
        manifest = Path(skill.root) / "SKILL.md"
        return self.approvals.record(
            approval_key(skill.source, skill.name), manifest.read_text(encoding="utf-8")
        )

    def _approval_issues(self, source: str, skill: Skill, text: str, path: Path) -> list[ValidationIssue]:
        """Refuse a third-party manifest that no longer matches its pin."""
        check = self.approvals.check(approval_key(source, skill.name), text)
        if check.status in ("unchanged", "first-sight"):
            return []
        if check.status == "unreadable":
            return [
                ValidationIssue(
                    "error",
                    f"the approval ledger ({self.approvals.path}) is unreadable, so this "
                    f"third-party skill cannot be trusted: repair or delete it, then run "
                    f"`iris skills approve {skill.name}`",
                    name=skill.name,
                    source=source,
                    path=str(path),
                )
            ]
        if check.status == "unapproved":
            return [
                ValidationIssue(
                    "error",
                    "has no approval pin and trust-on-first-use is off: review it and run "
                    f"`iris skills approve {skill.name}`",
                    name=skill.name,
                    source=source,
                    path=str(path),
                )
            ]
        return [
            ValidationIssue(
                "error",
                f"manifest changed since it was approved ({check.approved[:12]} → "
                f"{check.digest[:12]}): review it and run `iris skills approve {skill.name}` "
                "if the change is expected",
                name=skill.name,
                source=source,
                path=str(path),
            )
        ]

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
                    # Read once: the same text is parsed *and* digested when the
                    # source is third-party, and re-reading could pin content
                    # that differs from what was parsed.
                    text = path.read_text(encoding="utf-8")
                    parsed = (
                        parse_sidecar(json.loads(text), source=source)
                        if path.suffix == ".json"
                        else parse_skill_md(text, root=path.parent, source=source)
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
                if is_pinned(source):
                    # A pin failure is recorded but the candidate is still kept:
                    # the name must resolve to *this* copy (so `show`/`approve`
                    # can see it) rather than silently falling through to a
                    # lower-precedence skill of the same name.
                    issues.extend(self._approval_issues(source, parsed.skill, text, path))
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
