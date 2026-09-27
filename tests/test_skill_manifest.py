"""Skill manifests: the standard `SKILL.md` frontmatter and the legacy sidecar.

Both encodings must normalize into the *same* `Skill`, because the registry, the
policy and the JEV selector should never care which one a skill came from.
"""

from __future__ import annotations

import pytest

from iris_ai.memory.skills import Skill
from iris_ai.skills.manifest import (
    ManifestError,
    parse_sidecar,
    parse_skill_md,
    validate_skill,
)

SKILL_MD = """\
---
name: pdf-notes
description: Extract notes from a PDF and file them. Use when handling PDFs.
license: Apache-2.0
compatibility: Requires Python 3.12+
metadata:
  version: "1.2"
  iris-triggers: "pdf, extract notes"
allowed-tools: Read Write memory_search
---

# PDF notes

Step 1: open the document.
"""


def test_parses_the_standard_frontmatter():
    parsed = parse_skill_md(SKILL_MD, source="builtin")
    skill = parsed.skill
    assert skill.name == "pdf-notes"
    assert skill.description.startswith("Extract notes from a PDF")
    assert skill.license == "Apache-2.0"
    assert skill.compatibility == "Requires Python 3.12+"
    assert skill.version == "1.2"
    assert skill.triggers == ["pdf", "extract notes"]
    assert skill.allowed_tools == ["Read", "Write", "memory_search"]
    assert skill.source == "builtin"
    assert skill.procedure.startswith("# PDF notes")
    assert "---" not in skill.procedure  # the frontmatter is not part of the body
    assert parsed.issues == []


def test_space_separated_triggers_when_no_comma_is_used():
    text = SKILL_MD.replace("iris-triggers: \"pdf, extract notes\"", "iris-triggers: pdf notes")
    assert parse_skill_md(text).skill.triggers == ["pdf", "notes"]


def test_allowed_tools_accepts_a_yaml_list():
    text = SKILL_MD.replace("allowed-tools: Read Write memory_search", "allowed-tools: [Read, Write]")
    assert parse_skill_md(text).skill.allowed_tools == ["Read", "Write"]


def test_absent_allowed_tools_means_no_restriction():
    text = SKILL_MD.replace("allowed-tools: Read Write memory_search\n", "")
    assert parse_skill_md(text).skill.allowed_tools == []


def test_absent_metadata_is_fine():
    text = SKILL_MD.replace('metadata:\n  version: "1.2"\n  iris-triggers: "pdf, extract notes"\n', "")
    parsed = parse_skill_md(text)
    assert parsed.skill.version == ""
    assert parsed.skill.triggers == []


def test_markdown_without_frontmatter_cannot_be_a_skill():
    with pytest.raises(ManifestError):
        parse_skill_md("# just a document\n")


def test_unparsable_frontmatter_is_an_error():
    with pytest.raises(ManifestError):
        parse_skill_md("---\nname: [unclosed\n---\nbody\n")


@pytest.mark.parametrize(
    "name",
    [
        "PDF-Notes",  # uppercase
        "-pdf",  # leading hyphen
        "pdf-",  # trailing hyphen
        "pdf--notes",  # consecutive hyphens
        "p" * 65,  # too long
        "",  # empty
        "pdf notes",  # space
    ],
)
def test_name_shape_is_enforced(name):
    text = SKILL_MD.replace("name: pdf-notes", f"name: {name!r}")
    with pytest.raises(ManifestError):
        parse_skill_md(text)


def test_a_missing_description_cannot_be_a_skill():
    with pytest.raises(ManifestError):
        parse_skill_md(SKILL_MD.replace("description: Extract notes from a PDF and file them. Use when handling PDFs.\n", ""))


def test_unknown_frontmatter_keys_are_warnings_not_errors():
    text = SKILL_MD.replace("license: Apache-2.0\n", "license: Apache-2.0\nweird-key: 1\n")
    parsed = parse_skill_md(text)
    assert parsed.skill.name == "pdf-notes"
    assert [i.level for i in parsed.issues] == ["warning"]
    assert "weird-key" in parsed.issues[0].message


def test_directory_name_mismatch_is_a_warning(tmp_path):
    root = tmp_path / "other-name"
    root.mkdir()
    parsed = parse_skill_md(SKILL_MD, root=root)
    assert any("other-name" in i.message for i in parsed.issues)
    assert parsed.skill.root == str(root)


def test_sidecar_normalization_is_backward_compatible():
    """A sidecar written before P4 — the shape `skill_write` and dreaming produce —
    must load with the manifest defaults, without the caller knowing it is 'legacy'.
    """
    legacy = {
        "name": "svg-pro",
        "description": "Make an SVG the owner actually likes.",
        "triggers": ["svg"],
        "procedure": "Think in shapes.",
        "success_score": 0.7,
        "created": "2026-08-01T10:00:00",
        "updated": "2026-08-02T10:00:00",
    }
    parsed = parse_sidecar(legacy)
    skill = parsed.skill
    assert isinstance(skill, Skill)
    assert skill.source == "learned"
    assert skill.enabled is True
    assert skill.allowed_tools == []
    assert skill.timeout_seconds == 10.0
    assert skill.version == ""
    assert skill.success_score == 0.7
    assert parsed.issues == []


def test_sidecar_accepts_manifest_fields_when_present():
    parsed = parse_sidecar(
        {
            "name": "svg-pro",
            "description": "d",
            "procedure": "p",
            "enabled": False,
            "allowed_tools": ["Read"],
            "timeout_seconds": 30,
            "source": "package:iris-skills",
        }
    )
    assert parsed.skill.enabled is False
    assert parsed.skill.allowed_tools == ["Read"]
    assert parsed.skill.timeout_seconds == 30
    assert parsed.skill.source == "package:iris-skills"


def test_sidecar_keeps_a_legacy_underscore_name_with_a_warning():
    """`create_svg_art` is on disk right now. Spec-shaped names are enforced for
    new SKILL.md skills; for learned ones the only hard rule is safety, or the
    registry would reject skills Iris already taught herself."""
    parsed = parse_sidecar({"name": "create_svg_art", "description": "d", "procedure": "p"})
    assert parsed.skill.name == "create_svg_art"
    assert any(i.level == "warning" and "convention" in i.message for i in parsed.issues)


def test_sidecar_keeps_a_learned_prose_name():
    """The agent invents names in conversation ('Draft Standup'). The filename it
    lands in is sanitized, so the name only has to be free of path characters."""
    parsed = parse_sidecar({"name": "Draft Standup", "description": "d", "procedure": "p"})
    assert parsed.skill.name == "Draft Standup"


def test_sidecar_rejects_a_path_like_name():
    with pytest.raises(ManifestError):
        parse_sidecar({"name": "../escape", "description": "d"})


def test_sidecar_without_a_description_is_an_error():
    with pytest.raises(ManifestError):
        parse_sidecar({"name": "svg-pro", "procedure": "p"})


def test_validate_reports_an_unknown_tool():
    parsed = parse_skill_md(SKILL_MD)
    issues = validate_skill(parsed.skill, known_tools={"Read", "memory_search"})
    errors = [i for i in issues if i.level == "error"]
    assert errors and "Write" in errors[0].message


def test_validate_reports_a_missing_script(tmp_path):
    root = tmp_path / "pdf-notes"
    root.mkdir()
    parsed = parse_skill_md(SKILL_MD, root=root)
    skill = parsed.skill
    skill.scripts = ["scripts/gone.py"]
    issues = validate_skill(skill, known_tools=set())
    assert any(i.level == "error" and "gone.py" in i.message for i in issues)


def test_validate_accepts_a_clean_skill(tmp_path):
    root = tmp_path / "pdf-notes"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "extract.py").write_text("print('ok')\n", encoding="utf-8")
    parsed = parse_skill_md(SKILL_MD, root=root)
    parsed.skill.scripts = ["scripts/extract.py"]
    issues = validate_skill(parsed.skill, known_tools={"Read", "Write", "memory_search"})
    assert [i for i in issues if i.level == "error"] == []
