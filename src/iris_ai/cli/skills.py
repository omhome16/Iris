"""`iris skills` — read-only inspection of the skill registry.

Three verbs, all of them observation:

- `list` — every skill, from every source, with where it came from, whether it
  is enabled, its success score and how much of a surface it declares.
- `show <name>` — one skill's manifest and its full procedure (the thing the
  agent only ever sees after calling `skill_apply`).
- `validate` — every issue the registry found, plus name conflicts. **Exits 1 on
  anything error-level**, so it is usable as a gate; warnings (a legacy name, a
  shadowed builtin) print and still exit 0, because they are not defects.

Nothing here writes: the CLI has no enable/disable verb by design (see the P4
spec's deferrals).
"""

from __future__ import annotations

from pathlib import Path

from rich.table import Table

from iris_ai.agent.tools import TOOL_NAMES
from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.skills.registry import SkillRegistry, open_registry

# ValidationIssue levels ("error"/"warning") → the CLI's styles. Distinct from
# `help_theme.LEVEL_STYLE`, which names check *outcomes* (ok/warn/fail).
_ISSUE_STYLE = {"error": "iris.fail", "warning": "iris.warn"}


def registry_for_cli() -> SkillRegistry:
    return open_registry(WorkspaceFiles(Path(settings.workspace_dir)), known_tools=TOOL_NAMES)


def render_list(registry: SkillRegistry) -> None:
    out = console()
    skills = registry.list()
    if not skills:
        out.print("[iris.warn]no skills found[/iris.warn]")
        out.print(
            f"  looked in {registry.files.skills_dir()} and "
            f"{registry.builtin_dir or '(no builtin dir configured)'}"
        )
        return
    table = Table(title="Skills", title_style="iris.title", header_style="iris.title")
    table.add_column("name")
    table.add_column("source")
    table.add_column("on", justify="center")
    table.add_column("score", justify="right")
    table.add_column("tools")
    table.add_column("scripts", justify="right")
    for skill in skills:
        tools = ", ".join(skill.allowed_tools) if skill.allowed_tools else "(all)"
        table.add_row(
            skill.name,
            skill.source,
            "yes" if skill.enabled else "no",
            f"{skill.success_score:.2f}",
            tools,
            str(len(skill.scripts)),
        )
    out.print(table)
    conflicts = registry.conflicts
    if conflicts:
        out.print(f"[iris.warn]{len(conflicts)} name conflict(s)[/iris.warn] — `iris skills validate` lists them")


def render_show(registry: SkillRegistry, name: str) -> int:
    out = console()
    skill = registry.get(name)
    if skill is None:
        out.print(f"[iris.fail]no skill named {name!r}[/iris.fail]")
        known = ", ".join(s.name for s in registry.list()) or "(none)"
        out.print(f"known skills: {known}")
        return 1

    out.print(f"[iris.title]{skill.name}[/iris.title] [dim]({skill.source})[/dim]")
    out.print(f"  description: {skill.description}")
    for label, value in (
        ("triggers", ", ".join(skill.triggers) or "(none)"),
        ("enabled", "yes" if skill.enabled else "no"),
        ("success score", f"{skill.success_score:.2f}"),
        ("version", skill.version or "(unset)"),
        ("license", skill.license or "(unset)"),
        ("compatibility", skill.compatibility or "(unset)"),
        ("allowed tools", ", ".join(skill.allowed_tools) or "(unrestricted)"),
        ("timeout", f"{skill.timeout_seconds:g}s"),
        ("directory", skill.root or "(flat learned skill)"),
    ):
        out.print(f"  {label}: {value}")
    if skill.scripts:
        out.print(f"  scripts: {', '.join(skill.scripts)}")
    if skill.references:
        out.print(f"  references: {', '.join(skill.references)}")
    out.print("")
    out.print(skill.procedure or "(no procedure text)")
    return 0


def render_validate(registry: SkillRegistry) -> int:
    out = console()
    issues = registry.validate()
    if not issues:
        out.print("[iris.ok]skills: no issues[/iris.ok]")
        return 0
    errors = 0
    for issue in issues:
        if issue.level == "error":
            errors += 1
        style = _ISSUE_STYLE.get(issue.level, "iris.warn")
        where = f"{issue.name or '?'}"
        if issue.path:
            where += f" ({issue.path})"
        out.print(f"  [{style}]{issue.level:7}[/{style}] {where}: {issue.message}")
    summary = f"{len(issues) - errors} warning(s) · {errors} error(s)"
    style = "iris.fail" if errors else "iris.warn"
    out.print(f"[{style}]{summary}[/{style}]")
    return 1 if errors else 0


def run(action: str, name: str | None = None) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    try:
        registry = registry_for_cli()
    except Exception as exc:  # noqa: BLE001 - a broken workspace must not traceback
        out.print(f"[iris.fail]could not read the skill registry: {exc}[/iris.fail]")
        return 1

    match action:
        case "list":
            render_list(registry)
            return 0
        case "validate":
            return render_validate(registry)
        case "show":
            if not name:
                out.print("[iris.fail]`iris skills show` needs a skill name[/iris.fail]")
                return 2
            return render_show(registry, name)
        case _:
            out.print(f"[iris.fail]unknown action {action!r}[/iris.fail] — use list, show or validate")
            return 2
