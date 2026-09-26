"""skills — the skill registry: discovery, manifests, policy and the script boundary.

The *format* Iris speaks here is the open Agent Skills specification
(https://agentskills.io/specification): a directory with `SKILL.md`, YAML
frontmatter, and optional `scripts/`, `references/` and `assets/` directories.
The *concept* is unchanged from P2: a `Skill` is procedural memory, written by
dreaming and `skill_write`, selected by triggers or JEV.

`manifest` normalizes both encodings into one `Skill`; `registry` discovers and
reconciles them; `policy` enforces what an active skill may do; `runner` is the
only path by which a skill's code executes.
"""

from __future__ import annotations

from iris.skills.manifest import (
    ManifestError,
    ParsedSkill,
    ValidationIssue,
    parse_sidecar,
    parse_skill_md,
    validate_skill,
)

__all__ = [
    "ManifestError",
    "ParsedSkill",
    "ValidationIssue",
    "parse_sidecar",
    "parse_skill_md",
    "validate_skill",
]
