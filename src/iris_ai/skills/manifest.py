"""Skill manifests — two encodings, one `Skill`.

**Standard (Agent Skills spec).** A directory with `SKILL.md`: YAML frontmatter
(`name`, `description`, optional `license`, `compatibility`, `metadata`,
`allowed-tools`) followed by the instructions, plus optional `scripts/`,
`references/` and `assets/` directories.

**Learned (Iris's own, unchanged since v0.2).** A flat pair in the workspace:
`<name>.md` with the procedure and `<name>.json` with the metadata sidecar —
what `skill_write`, dreaming and the reinforce/revise loop produce.

Both come out of here as the same dataclass, so the registry, the policy, the
JEV selector and the tools never branch on which one a skill is.

Two rules shape the validation:

- **Identity errors raise `ManifestError`** (no frontmatter, unparsable YAML,
  missing/ill-formed name, missing description). A file that cannot be a skill
  should not silently become one.
- **Everything else is a `ValidationIssue`** — warnings for spec deviations
  (unknown keys, a non-`allowed-tools`-shaped field, no triggers), errors for
  things that would make the skill unsafe or unusable at call time (a tool that
  does not exist, a script that is missing or escapes its directory).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from iris_ai.memory.skills import Skill

# The spec's name rule: lowercase alphanumerics and single hyphens, 1..64 chars.
SPEC_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024

# Keys the spec defines, plus the iris extensions we read at the top level.
_SPEC_KEYS = {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
_IRIS_KEYS = {"allowed_tools", "triggers", "version", "enabled", "timeout_seconds"}
_KNOWN_KEYS = _SPEC_KEYS | _IRIS_KEYS

# Legacy sidecar names predate the spec and are already on disk — including
# ones the agent invented mid-conversation (`create_svg_art`, `Draft Standup`).
# They only have to be *safe*, not spec-shaped: `WorkspaceFiles.skill_path`
# sanitizes the filename it derives, so the name itself is free-form prose and
# the only hard rule is that it cannot look like a path.
_PATH_SEPARATORS = re.compile(r"[/\\]")
MAX_LEGACY_NAME = 120


class ManifestError(Exception):
    """The input cannot be a skill at all (identity, not quality)."""


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    level: str  # "error" | "warning"
    message: str
    name: str = ""
    source: str = ""
    path: str = ""

    def __str__(self) -> str:
        where = f"{self.name or '?'}"
        if self.source:
            where += f" [{self.source}]"
        return f"{self.level}: {where}: {self.message}"


@dataclass(slots=True)
class ParsedSkill:
    skill: Skill
    issues: list[ValidationIssue]


def split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Frontmatter mapping + Markdown body. Raises `ManifestError`."""
    cleaned = text.lstrip("\ufeff")
    lines = cleaned.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ManifestError("no YAML frontmatter (a SKILL.md must start with '---')")
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        raise ManifestError("YAML frontmatter is never closed with '---'")
    raw = "\n".join(lines[1:end])
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # pragma: no cover - message text is the point
        raise ManifestError(f"invalid YAML frontmatter: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ManifestError("YAML frontmatter must be a mapping of keys to values")
    return data, "\n".join(lines[end + 1 :])


def _as_list(value: Any) -> list[str]:
    """`Read Write` (the spec's string form), `[Read, Write]`, or a single name."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part for part in str(value).replace(",", " ").split() if part]


def _metadata(data: dict[str, Any]) -> dict[str, str]:
    meta = data.get("metadata") or {}
    if not isinstance(meta, dict):
        return {}
    return {str(k): str(v) for k, v in meta.items()}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", []):
            return value
    return None


def _triggers(data: dict[str, Any], meta: dict[str, str]) -> list[str]:
    """`metadata.iris-triggers: "a, b"` or `triggers: [a, b]`.

    A comma means the author is separating phrases; without one, whitespace is
    the separator — the same convention the spec's `allowed-tools` uses.
    """
    raw = _first(meta.get("iris-triggers"), data.get("triggers"))
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(v).strip() for v in raw if str(v).strip()]
    text = str(raw)
    parts = text.split(",") if "," in text else text.split()
    return [p.strip() for p in parts if p.strip()]


def parse_skill_md(
    text: str,
    *,
    root: Path | str | None = None,
    source: str = "builtin",
) -> ParsedSkill:
    data, body = split_frontmatter(text)
    issues: list[ValidationIssue] = []

    name = str(data.get("name") or "").strip()
    if not name:
        raise ManifestError("frontmatter is missing the required 'name'")
    if not SPEC_NAME_RE.match(name) or len(name) > MAX_NAME:
        raise ManifestError(
            f"invalid skill name {name!r}: lowercase letters, digits and single hyphens, max {MAX_NAME} chars"
        )
    description = str(data.get("description") or "").strip()
    if not description:
        raise ManifestError("frontmatter is missing the required 'description'")

    meta = _metadata(data)
    for key in data:
        if key not in _KNOWN_KEYS:
            issues.append(
                ValidationIssue(
                    "warning",
                    f"unknown frontmatter key {key!r} (ignored; use `metadata:` for extensions)",
                    name=name,
                    source=source,
                )
            )

    root_path = Path(root) if root is not None else None
    if root_path is not None and root_path.name != name:
        issues.append(
            ValidationIssue(
                "warning",
                f"directory name {root_path.name!r} does not match the skill name {name!r}",
                name=name,
                source=source,
            )
        )

    enabled_raw = _first(data.get("enabled"), meta.get("iris-enabled"))
    skill = Skill(
        name=name,
        description=description,
        triggers=_triggers(data, meta),
        procedure=body.strip(),
        license=str(data.get("license") or ""),
        compatibility=str(data.get("compatibility") or ""),
        version=str(_first(data.get("version"), meta.get("version")) or ""),
        source=source,
        enabled=True if enabled_raw is None else bool(enabled_raw),
        allowed_tools=_as_list(_first(data.get("allowed-tools"), data.get("allowed_tools"))),
        timeout_seconds=float(_first(data.get("timeout_seconds"), meta.get("iris-timeout")) or 10.0),
        root=str(root_path) if root_path is not None else "",
        metadata=meta,
        scripts=_scan(root_path / "scripts") if root_path else [],
        references=_scan(root_path / "references") if root_path else [],
    )
    if len(description) > MAX_DESCRIPTION:
        issues.append(
            ValidationIssue(
                "warning",
                f"description is {len(description)} chars (spec maximum is {MAX_DESCRIPTION})",
                name=name,
                source=source,
            )
        )
    if not skill.triggers:
        issues.append(
            ValidationIssue(
                "warning",
                "no triggers: the deterministic matcher can never select this skill (JEV still can)",
                name=name,
                source=source,
            )
        )
    return ParsedSkill(skill=skill, issues=issues)


def parse_sidecar(data: dict[str, Any], *, source: str = "learned") -> ParsedSkill:
    """Legacy `skills/<name>.json` → `Skill`. Raises `ManifestError`."""
    if not isinstance(data, dict):
        raise ManifestError("sidecar must be a JSON object")
    name = str(data.get("name") or "").strip()
    if not name:
        raise ManifestError("sidecar is missing 'name'")
    if _PATH_SEPARATORS.search(name) or name in (".", "..") or "\x00" in name or len(name) > MAX_LEGACY_NAME:
        raise ManifestError(f"unsafe skill name {name!r}")
    description = str(data.get("description") or "").strip()
    if not description:
        raise ManifestError("sidecar is missing 'description'")

    issues: list[ValidationIssue] = []
    if not SPEC_NAME_RE.match(name):
        # Not fatal: skills learned before the spec landed are on disk right now.
        issues.append(
            ValidationIssue(
                "warning",
                f"name {name!r} does not match the Agent Skills convention (lowercase + hyphens)",
                name=name,
                source=source,
            )
        )

    known = {f.name for f in fields(Skill)}
    for key in data:
        if key not in known:
            issues.append(
                ValidationIssue(
                    "warning",
                    f"unknown sidecar key {key!r} (ignored)",
                    name=name,
                    source=source,
                )
            )

    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if key not in known:
            continue
        kwargs[key] = value
    kwargs["name"] = name
    kwargs["description"] = description
    kwargs.setdefault("triggers", [])
    kwargs.setdefault("procedure", "")
    kwargs.setdefault("source", source)
    kwargs.setdefault("allowed_tools", [])
    kwargs.setdefault("timeout_seconds", 10.0)
    kwargs["allowed_tools"] = _as_list(kwargs.get("allowed_tools"))
    kwargs["triggers"] = [str(t) for t in (kwargs.get("triggers") or [])]
    return ParsedSkill(skill=Skill(**kwargs), issues=issues)


def _scan(directory: Path) -> list[str]:
    """Relative POSIX paths of every file under `directory` (empty if absent)."""
    if not directory.is_dir():
        return []
    return sorted(
        p.relative_to(directory.parent).as_posix() for p in directory.rglob("*") if p.is_file()
    )


def _unsafe_relative(rel: str) -> str | None:
    parts = Path(rel).parts
    if not rel or Path(rel).is_absolute() or "\\" in rel or ".." in parts or "~" in parts:
        return f"unsafe path {rel!r} (must be relative, inside the skill directory)"
    return None


def validate_skill(skill: Skill, *, known_tools: set[str] | None = None) -> list[ValidationIssue]:
    """Cross-checks that need more than the skill itself.

    `known_tools` is the runtime's tool registry: a manifest naming a tool that
    does not exist is an **error**, because otherwise a typo silently becomes an
    invisible permission restriction (or worse, looks like a granted power).
    """
    issues: list[ValidationIssue] = []
    name, source = skill.name, skill.source

    if known_tools is not None:
        for tool in skill.allowed_tools:
            if tool not in known_tools:
                issues.append(
                    ValidationIssue(
                        "error",
                        f"allowed-tools names an unknown tool {tool!r} (known: {', '.join(sorted(known_tools))})",
                        name=name,
                        source=source,
                    )
                )
    for script in skill.scripts:
        problem = _unsafe_relative(script)
        if problem:
            issues.append(ValidationIssue("error", problem, name=name, source=source))
            continue
        if skill.root and not (Path(skill.root) / script).is_file():
            issues.append(
                ValidationIssue("error", f"script {script!r} is listed but missing", name=name, source=source)
            )
    for reference in skill.references:
        problem = _unsafe_relative(reference)
        if problem:
            issues.append(ValidationIssue("error", problem, name=name, source=source))
    if skill.timeout_seconds <= 0:
        issues.append(
            ValidationIssue("error", "timeout_seconds must be positive", name=name, source=source)
        )
    if len(skill.description) > MAX_DESCRIPTION:
        issues.append(
            ValidationIssue(
                "warning",
                f"description is {len(skill.description)} chars (spec maximum is {MAX_DESCRIPTION})",
                name=name,
                source=source,
            )
        )
    return issues
