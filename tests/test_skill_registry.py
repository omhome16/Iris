"""The skill registry: discovery across sources, precedence, and conflicts.

The property under test is *visibility*: every skill that exists is either
selectable or reported, and nothing is ever silently dropped. A registry that
quietly prefers one of two same-named skills is how an owner loses trust in the
skill tier without ever seeing an error.
"""

from __future__ import annotations

import pytest

from iris_ai.memory.files import WorkspaceFiles
from iris_ai.memory.skills import Skill, SkillLibrary
from iris_ai.skills.registry import SkillRegistry

SKILL_MD = """\
---
name: {name}
description: {description}
metadata:
  iris-triggers: "{trigger}"
---

# {name}

Do the thing.
"""


def _write_standard(root, name: str, *, description: str = "Does a thing.", trigger: str = "thing", enabled: bool = True):
    """A spec-format skill directory under `root`."""
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    text = SKILL_MD.format(name=name, description=description, trigger=trigger)
    if not enabled:
        text = text.replace("---\n", "---\nenabled: false\n", 1)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")
    return directory


def _write_learned(files: WorkspaceFiles, name: str, *, score: float = 0.5) -> SkillLibrary:
    library = SkillLibrary(files)
    library.write(Skill(name=name, description="Learned thing.", triggers=["learned"], procedure="p", success_score=score))
    return library


def _registry(files: WorkspaceFiles, **kwargs) -> SkillRegistry:
    kwargs.setdefault("builtin_dir", None)
    kwargs.setdefault("entry_points", lambda: [])
    return SkillRegistry(files, **kwargs)


def test_discovers_learned_workspace_skills(tmp_path):
    files = WorkspaceFiles(tmp_path)
    _write_learned(files, "svg-pro")
    names = {s.name: s for s in _registry(files).list()}
    assert "svg-pro" in names
    assert names["svg-pro"].source == "learned"
    assert names["svg-pro"].success_score == 0.5


def test_discovers_standard_skill_directories_in_the_workspace(tmp_path):
    files = WorkspaceFiles(tmp_path)
    _write_standard(files.skills_dir(), "pdf-notes")
    registry = _registry(files)
    skill = registry.get("pdf-notes")
    assert skill is not None
    assert skill.source == "workspace"
    assert skill.triggers == ["thing"]
    assert skill.root.endswith("pdf-notes")


def test_discovers_builtin_and_extra_roots(tmp_path):
    files = WorkspaceFiles(tmp_path / "workspace")
    builtin = tmp_path / "builtin"
    extra = tmp_path / "extra"
    _write_standard(builtin, "from-builtin")
    _write_standard(extra, "from-extra")
    registry = _registry(files, builtin_dir=builtin, extra_dirs=[extra])
    sources = {s.name: s.source for s in registry.list()}
    assert sources == {"from-builtin": "builtin", "from-extra": "extra"}


def test_discovers_package_entry_points(tmp_path):
    files = WorkspaceFiles(tmp_path / "workspace")
    packages = tmp_path / "site-packages" / "some-pack"
    _write_standard(packages, "from-a-package")
    registry = _registry(files, entry_points=lambda: [("iris-extras", packages)])
    skill = registry.get("from-a-package")
    assert skill is not None
    assert skill.source == "package:iris-extras"


def test_the_default_entry_point_reader_is_safe(tmp_path):
    """No installed distribution advertises `iris_ai.skills` today; discovery must
    simply find nothing rather than raise."""
    files = WorkspaceFiles(tmp_path)
    registry = SkillRegistry(files, builtin_dir=None)
    assert registry.list() == []
    assert registry.validate() == []


def test_precedence_workspace_beats_package_beats_builtin(tmp_path):
    files = WorkspaceFiles(tmp_path / "workspace")
    builtin = tmp_path / "builtin"
    packages = tmp_path / "packages"
    _write_standard(builtin, "shared", description="builtin copy")
    _write_standard(packages, "shared", description="package copy")
    _write_standard(files.skills_dir(), "shared", description="workspace copy")
    registry = _registry(files, builtin_dir=builtin, entry_points=lambda: [("pack", packages)])

    winner = registry.get("shared")
    assert winner is not None
    assert winner.source == "workspace"
    assert winner.description == "workspace copy"
    # …and both losers are named, not dropped in silence.
    losers = {(c.loser, c.winner) for c in registry.conflicts if c.name == "shared"}
    assert losers == {("package:pack", "workspace"), ("builtin", "workspace")}


def test_conflicts_carry_where_each_copy_lives(tmp_path):
    files = WorkspaceFiles(tmp_path / "workspace")
    builtin = tmp_path / "builtin"
    _write_standard(builtin, "shared")
    _write_standard(files.skills_dir(), "shared")
    registry = _registry(files, builtin_dir=builtin)
    conflict = next(c for c in registry.conflicts if c.name == "shared")
    assert conflict.winner == "workspace"
    assert conflict.loser == "builtin"
    assert conflict.path.endswith("SKILL.md")


def test_a_disabled_skill_is_listed_but_not_selectable(tmp_path):
    files = WorkspaceFiles(tmp_path)
    _write_standard(files.skills_dir(), "off-by-default", enabled=False)
    _write_standard(files.skills_dir(), "on-by-default")
    registry = _registry(files)
    assert {s.name for s in registry.list()} == {"off-by-default", "on-by-default"}
    assert [s.name for s in registry.selectable()] == ["on-by-default"]


def test_a_malformed_skill_is_excluded_and_reported(tmp_path):
    files = WorkspaceFiles(tmp_path)
    broken = files.skills_dir() / "broken"
    broken.mkdir()
    (broken / "SKILL.md").write_text("---\nname: Broken_Name\ndescription: d\n---\nbody\n", encoding="utf-8")
    registry = _registry(files)
    assert registry.get("broken") is None
    assert registry.list() == []
    errors = [i for i in registry.validate() if i.level == "error"]
    assert errors and "Broken_Name" in errors[0].message
    assert errors[0].path.endswith("SKILL.md")


def test_unreadable_sidecars_are_reported_not_ignored(tmp_path):
    files = WorkspaceFiles(tmp_path)
    (files.skills_dir() / "half.json").write_text("{ not json", encoding="utf-8")
    registry = _registry(files)
    assert registry.list() == []
    assert any(i.level == "error" and "half" in (i.path or i.name) for i in registry.validate())


def test_ordering_is_deterministic(tmp_path):
    files = WorkspaceFiles(tmp_path)
    builtin = tmp_path / "builtin"
    _write_standard(files.skills_dir(), "zeta")
    _write_standard(builtin, "alpha")
    _write_standard(builtin, "mid")
    registry = _registry(files, builtin_dir=builtin)
    # workspace first, then builtins, alphabetical inside a source.
    assert [s.name for s in registry.list()] == ["zeta", "alpha", "mid"]


def test_a_missing_root_is_not_an_error(tmp_path):
    files = WorkspaceFiles(tmp_path)
    registry = _registry(files, builtin_dir=tmp_path / "does-not-exist")
    assert registry.list() == []
    assert registry.validate() == []


def test_validate_reports_unknown_tools(tmp_path):
    files = WorkspaceFiles(tmp_path)
    directory = files.skills_dir() / "wants-more"
    directory.mkdir()
    (directory / "SKILL.md").write_text(
        "---\nname: wants-more\ndescription: d\nallowed-tools: Read Teleport\n---\nbody\n",
        encoding="utf-8",
    )
    registry = _registry(files, known_tools={"Read", "memory_search"})
    errors = [i for i in registry.validate() if i.level == "error"]
    assert any("Teleport" in i.message for i in errors)


def test_reinforce_updates_a_learned_skill_through_the_registry(tmp_path):
    files = WorkspaceFiles(tmp_path)
    _write_learned(files, "svg-pro")
    registry = _registry(files)
    assert registry.reinforce("svg-pro").success_score == 0.6
    assert registry.get("svg-pro").success_score == 0.6


def test_reinforce_refuses_a_read_only_source(tmp_path):
    """Improving a shipped skill must not quietly create a shadowing half-copy."""
    files = WorkspaceFiles(tmp_path / "workspace")
    builtin = tmp_path / "builtin"
    _write_standard(builtin, "pdf-notes")
    registry = _registry(files, builtin_dir=builtin)
    with pytest.raises(PermissionError, match="only learned skills"):
        registry.reinforce("pdf-notes")
    assert not (files.skills_dir() / "pdf-notes.json").exists()


def test_write_still_lands_in_the_workspace(tmp_path):
    files = WorkspaceFiles(tmp_path)
    registry = _registry(files)
    registry.write(Skill(name="new-skill", description="d", procedure="p"))
    assert (files.skills_dir() / "new-skill.json").exists()
    assert registry.get("new-skill").source == "learned"


def test_reinforce_refuses_a_hand_authored_directory_skill(tmp_path):
    """A spec-format skill has no sidecar, so `SkillLibrary.reinforce` would
    silently do nothing. Better to say why than to return None."""
    files = WorkspaceFiles(tmp_path)
    _write_standard(files.skills_dir(), "mine")
    registry = _registry(files)
    with pytest.raises(PermissionError, match="only learned skills"):
        registry.reinforce("mine")


def test_the_shipped_builtin_skill_is_discovered_and_valid(tmp_path):
    """The repo's own `skills/` directory is the proof the standard format works:
    if the format or the validator drifts, this is what notices."""
    from iris_ai.agent.tools import TOOL_NAMES
    from iris_ai.skills.registry import repo_root

    files = WorkspaceFiles(tmp_path)
    registry = SkillRegistry(
        files,
        builtin_dir=repo_root() / "skills",
        entry_points=lambda: [],
        known_tools=TOOL_NAMES,
    )
    skill = registry.get("web-page-to-notes")
    assert skill is not None, [str(i) for i in registry.validate()]
    assert skill.source == "builtin"
    assert skill.scripts == ["scripts/extract.py"]
    assert skill.triggers, "a builtin with no triggers can never be selected deterministically"
    assert [i for i in registry.validate() if i.level == "error"] == []


async def test_the_builtin_skills_script_actually_runs(tmp_path):
    """A shipped script that cannot run is documentation, not a capability."""
    from iris_ai.skills.registry import repo_root
    from iris_ai.skills.runner import run_script

    files = WorkspaceFiles(tmp_path)
    registry = SkillRegistry(files, builtin_dir=repo_root() / "skills", entry_points=lambda: [])
    skill = registry.get("web-page-to-notes")
    page = tmp_path / "page.html"
    page.write_text(
        "<html><head><title>On Sanding</title><style>b{}</style></head>"
        "<body><h1>On Sanding</h1><p>Sand with the grain.</p>"
        "<script>console.log('ignore me')</script><p>Then wipe it down.</p></body></html>",
        encoding="utf-8",
    )
    out = tmp_path / "page.md"
    result = await run_script(skill, "scripts/extract.py", args=[str(page), str(out)])
    assert result.ok is True, result.error + result.stderr
    notes = out.read_text(encoding="utf-8")
    assert "Sand with the grain." in notes
    assert "console.log" not in notes  # script content is skipped, not text
    assert notes.startswith("# On Sanding")


def test_the_registry_never_caches_away_a_new_skill(tmp_path):
    """Writes stay owned by `SkillLibrary` (dreaming, skill_write, reinforce):
    the registry must observe them, not shadow them with a stale snapshot."""
    files = WorkspaceFiles(tmp_path)
    registry = _registry(files)
    assert registry.list() == []
    library = _write_learned(files, "fresh")
    assert [s.name for s in registry.list()] == ["fresh"]
    library.reinforce("fresh")
    assert registry.get("fresh").success_score > 0.5
