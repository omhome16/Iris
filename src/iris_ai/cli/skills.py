"""`iris skills` — inspection of the skill registry, plus one trust verb.

Four verbs:

- `list` — every skill, from every source, with where it came from, whether it
  is usable, its success score and how much of a surface it declares.
- `show <name>` — one skill's manifest and its full procedure (the thing the
  agent only ever sees after calling `skill_apply`).
- `validate` — every issue the registry found, plus name conflicts. **Exits 1 on
  anything error-level**, so it is usable as a gate; warnings (a legacy name, a
  shadowed builtin) print and still exit 0, because they are not defects.
- `approve <name>` — the one writer: re-pin a third-party manifest whose digest
  changed. Trust-on-first-use (`iris_ai/skills/approvals.py`) means a *changed*
  `package:`/`extra` skill is refused until an owner says so here. It is a
  person's decision by design: a model that could approve its own injected
  instructions would make the pin decorative.

There is still no enable/disable verb: `enabled:` belongs in the manifest (see
`Skill.enabled`), and the P4 spec defers a mutable override store.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.agent.tools import TOOL_NAMES
from iris_ai.cli import ui
from iris_ai.cli.help_theme import console
from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.skills.approvals import ApprovalLedgerError
from iris_ai.skills.registry import SkillRegistry, open_registry


def registry_for_cli() -> SkillRegistry:
    return open_registry(WorkspaceFiles(Path(settings.workspace_dir)), known_tools=TOOL_NAMES)


def render_list(registry: SkillRegistry) -> None:
    out = console()
    ui.header(out, "Skills", "every skill from every source, whether it is usable, and its record")
    skills = registry.list()
    if not skills:
        ui.warn(out, "no skills found")
        ui.note(
            out,
            f"looked in {registry.files.skills_dir()} and "
            f"{registry.builtin_dir or '(no builtin dir configured)'}",
        )
        return
    # "on" is *usable*, not `enabled:` as written: a skill the selector will
    # never offer (a failed approval pin, an unknown tool) must not read as on.
    usable = {(s.name, s.source) for s in registry.selectable()}
    table = ui.table(
        out,
        "",
        [
            "name",
            "source",
            ("on", {"justify": "center"}),
            ("score", {"justify": "right"}),
            "tools",
            ("scripts", {"justify": "right"}),
        ],
    )
    withheld = 0
    for skill in skills:
        tools = ", ".join(skill.allowed_tools) if skill.allowed_tools else "(all)"
        on = (skill.name, skill.source) in usable
        if not on and skill.enabled:
            withheld += 1
        table.add_row(
            skill.name,
            skill.source,
            "[iris.ok]yes[/iris.ok]" if on else "[iris.sub]no[/iris.sub]",
            f"{skill.success_score:.2f}",
            tools,
            str(len(skill.scripts)),
        )
    out.print(table)
    if withheld:
        ui.warn(
            out,
            f"{withheld} enabled skill(s) cannot be selected — `iris skills validate` says why",
        )
    conflicts = registry.conflicts
    if conflicts:
        ui.warn(out, f"{len(conflicts)} name conflict(s) — `iris skills validate` lists them")


def render_show(registry: SkillRegistry, name: str) -> int:
    out = console()
    skill = registry.get(name)
    if skill is None:
        ui.error(out, f"no skill named {name!r}")
        known = ", ".join(s.name for s in registry.list()) or "(none)"
        ui.note(out, f"known skills: {known}")
        return 1

    ui.header(out, skill.name, f"{skill.source} skill - {skill.description}")
    rows: list[tuple[str, str]] = [
        ("triggers", ", ".join(skill.triggers) or "(none)"),
        ("enabled", "yes" if skill.enabled else "no"),
        ("success score", f"{skill.success_score:.2f}"),
        ("version", skill.version or "(unset)"),
        ("license", skill.license or "(unset)"),
        ("compatibility", skill.compatibility or "(unset)"),
        ("allowed tools", ", ".join(skill.allowed_tools) or "(unrestricted)"),
        ("timeout", f"{skill.timeout_seconds:g}s"),
        ("directory", skill.root or "(flat learned skill)"),
    ]
    if skill.scripts:
        rows.append(("scripts", ", ".join(skill.scripts)))
    if skill.references:
        rows.append(("references", ", ".join(skill.references)))
    ui.grid(out, rows)
    ui.section(out, "procedure")
    out.print(skill.procedure or "(no procedure text)")
    return 0


def render_validate(registry: SkillRegistry) -> int:
    out = console()
    issues = registry.validate()
    if not issues:
        out.print("[iris.ok]skills: no issues[/iris.ok]")
        return 0
    ui.header(out, "Skills validate", "every issue the registry found; error-level ones fail the command")
    errors = 0
    for issue in issues:
        level = "fail" if issue.level == "error" else "warn"
        if issue.level == "error":
            errors += 1
        where = f"{issue.name or '?'}"
        if issue.path:
            where += f" ({issue.path})"
        ui.status(out, level, where, issue.message, indent=0)
    summary = f"{len(issues) - errors} warning(s) · {errors} error(s)"
    style = "iris.fail" if errors else "iris.warn"
    out.print()
    out.print(f"[{style}]{summary}[/{style}]")
    return 1 if errors else 0


def render_approve(registry: SkillRegistry, name: str) -> int:
    """Re-pin a third-party manifest (the rug-pull remedy)."""
    out = console()
    try:
        record = registry.approve(name)
    except ApprovalLedgerError as exc:
        ui.failed(out, "the approval ledger is unreadable:", str(exc))
        ui.note(
            out,
            "repair it, or delete it — every third-party skill is re-pinned on "
            "first sighting (trust on first use), which re-trusts what is on disk now",
        )
        return 1
    except (OSError, UnicodeDecodeError) as exc:
        ui.failed(out, f"could not read the manifest for {name!r}:", str(exc))
        return 1
    if record is None:
        skill = registry.get(name)
        if skill is None:
            ui.error(out, f"no skill named {name!r}")
            known = ", ".join(s.name for s in registry.list()) or "(none)"
            ui.note(out, f"known skills: {known}")
        else:
            ui.failed(
                out,
                f"{name!r} is a {skill.source} skill:",
                "only third-party sources (package:, extra) are hash-pinned, so there "
                "is nothing to re-approve",
            )
        return 1
    out.print(f"[iris.ok]approved[/iris.ok] {record.key} -> {record.digest[:12]}")
    return 0


def run(action: str, name: str | None = None) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    try:
        registry = registry_for_cli()
    except Exception as exc:  # noqa: BLE001 - a broken workspace must not traceback
        ui.error(out, f"could not read the skill registry: {exc}")
        return 1

    match action:
        case "list":
            render_list(registry)
            return 0
        case "validate":
            return render_validate(registry)
        case "show":
            if not name:
                ui.error(out, "`iris skills show` needs a skill name")
                return 2
            return render_show(registry, name)
        case "approve":
            if not name:
                ui.error(out, "`iris skills approve` needs a skill name")
                return 2
            return render_approve(registry, name)
        case _:
            ui.failed(out, f"unknown action {action!r}", "- use list, show, validate or approve")
            return 2
