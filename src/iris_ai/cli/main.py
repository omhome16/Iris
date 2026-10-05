"""Iris CLI root — typer app bound to [project.scripts] iris.

The help is **generated from the command registry**, not written by hand. A
hand-written help screen is a second list of commands, and the two drift: the
screen keeps advertising a verb that was renamed and silently omits the one that
was added. `_help_screen` walks the click group it is called on, so a command that
exists is a command that is listed, and the test that asserts the exact command
set is asserting the thing the user reads.

The screen is grouped by what a person is trying to do (start, configure,
inspect, maintain) rather than alphabetically. Commands are grouped by hand,
because "which of these is a *setup* verb" is a judgment; a command missing from
the grouping still appears, under its own heading, so the list is never partial.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import typer

from iris_ai.cli import art, ui
from iris_ai.cli import version as version_mod
from iris_ai.cli.help_theme import console


def _load(name: str):
    """Import a command module only when that command runs.

    Importing every command at startup pulled in the engine and the model
    client, which made `iris --help` take several seconds.
    """
    import importlib

    return importlib.import_module(f"iris_ai.cli.{name}")

app = typer.Typer(
    name="iris",
    help="Iris — personal agent harness (library + CLI).",
    invoke_without_command=True,
    add_completion=False,
    # The root help is drawn by `_help_screen` instead: one screen, in this
    # package's palette, always in sync with the registry. Subcommands keep the
    # default help, which is where the per-flag detail belongs.
    add_help_option=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

#: Which commands belong together, in reading order. A command that is not named
#: here is listed under `More` — the screen degrades by getting longer, never by
#: dropping a verb.
_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("start", ("init", "chat", "serve", "config", "doctor", "version")),
    ("configure", ("secrets", "mcp", "models", "components", "policy", "new")),
    ("inspect", ("tools", "plugins", "guards", "costs", "agents", "skills", "cron", "trace", "eval", "evolve", "memory", "bench")),
    ("maintain", ("migrate", "core")),
)

_TAGLINE = "memory · judgment · agents — one library, one kernel, four faces"

# Written by the root callback; read by commands that need --debug state.
# click only guarantees root params exist during callback execution, so we
# stash the resolved flag instead of reading it lazily inside subcommands.
_debug_flag = False


def _debug_enabled(debug: bool) -> bool:
    return debug or os.environ.get("IRIS_DEBUG", "") == "1"


def _fail(message: str, debug: bool, *, cause: BaseException | None = None) -> None:
    """Print a friendly error; under debug re-raise `cause` for a traceback."""
    out = console()
    ui.failed(out, "error", message)
    if debug:
        if cause is not None:
            raise cause  # original traceback (spec: --debug / IRIS_DEBUG=1)
        raise typer.Exit(code=1)
    ui.hint(out, "hint: re-run with --debug or IRIS_DEBUG=1 for a traceback")
    raise typer.Exit(code=1)


def _command_help(ctx: typer.Context) -> dict[str, str]:
    """`name -> one-line summary`, read off the live command registry.

    Click owns the summaries (`get_short_help_str`), so a docstring edit updates
    this screen with no second copy to remember.
    """
    group = getattr(ctx.command, "commands", None) or {}
    return {name: command.get_short_help_str(limit=60) for name, command in group.items()}


def _grouped(commands: dict[str, str]) -> list[tuple[str, list[tuple[str, str]]]]:
    """The registry, arranged into the reading order above (never a subset)."""
    groups: list[tuple[str, list[tuple[str, str]]]] = []
    placed: set[str] = set()
    for label, names in _GROUPS:
        rows = [(name, commands[name]) for name in names if name in commands]
        if rows:
            groups.append((label, rows))
            placed.update(name for name, _ in rows)
    leftover = sorted(name for name in commands if name not in placed)
    if leftover:
        groups.append(("more", [(name, commands[name]) for name in leftover]))
    return groups


def _help_screen(ctx: typer.Context, *, draw_art: bool = True) -> None:
    """The start screen: the mark, then every command, grouped by intent."""
    out = console()
    if draw_art and art.banner_enabled(out):
        art.render_banner(out, subtitle=version_mod.version_lines()[0], tagline=_TAGLINE)

    ui.section(out, "commands")
    for label, rows in _grouped(_command_help(ctx)):
        out.print(f"  [iris.sub]{label}[/iris.sub]")
        for name, summary in rows:
            out.print(f"    [iris.cmd]{name}[/iris.cmd]  [iris.sub]{summary}[/iris.sub]")

    out.print()
    ui.steps(
        out,
        "next",
        [
            ("iris init", "provider, key, model, then a working profile"),
            ("iris", "open the chat — no service, no daemon"),
        ],
        subtitle="the flags of one command: `iris <command> --help`",
    )
    ui.note(out, "docs: DOCS.md  ·  state lives in workspace/  ·  no server required")


@app.callback()
def root(
    ctx: typer.Context,
    help_: bool = typer.Option(
        False, "--help", "-h", help="Show this screen and exit.", is_eager=True
    ),
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit.", is_eager=True
    ),
    debug: bool = typer.Option(False, "--debug", help="Full tracebacks on error."),
) -> None:
    global _debug_flag
    _debug_flag = _debug_enabled(debug)
    if help_:
        _help_screen(ctx)
        raise typer.Exit()
    if version:
        version_mod.print_version()
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        if sys.stdout.isatty() and not os.environ.get("IRIS_PLAIN"):
            chat_mod = _load("chat")
            raise typer.Exit(code=chat_mod.run_chat())
        _help_screen(ctx)
        raise typer.Exit()


@app.command()
def chat(
    session: str = typer.Option("cli", "--session", help="Conversation thread id (memory continuity)."),
    once: str | None = typer.Option(None, "--once", help="Run a single turn and exit."),
    no_banner: bool = typer.Option(False, "--no-banner", help="Skip the start-screen art."),
) -> None:
    """Chat in the terminal (the TUI, or one turn with --once)."""
    chat_mod = _load("chat")
    raise typer.Exit(
        code=chat_mod.run_chat(session=session, once=once, debug=_debug_flag, no_banner=no_banner)
    )


@app.command()
def skills(
    action: str = typer.Argument("list", help="list | show <name> | validate | approve <name>"),
    name: str | None = typer.Argument(None, help="Skill name (for `show`/`approve`)."),
) -> None:
    """Inspect the skill registry: list, show, validate, approve."""
    skills_mod = _load("skills")
    raise typer.Exit(code=skills_mod.run(action=action, name=name))


@app.command()
def agents(
    action: str = typer.Argument("roles", help="roles | show <name> | handoffs"),
    name: str | None = typer.Argument(None, help="Role name (for `show`)."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many decisions to list."),
) -> None:
    """Inspect the multi-agent layer (read-only): roles, show, handoffs."""
    agents_mod = _load("agents")
    raise typer.Exit(code=agents_mod.run(action=action, name=name, limit=limit))


@app.command()
def cron(
    action: str = typer.Argument("list", help="list | add | rm <id>"),
    job_id: str = typer.Argument("", help="Job id (for `rm`)."),
    once: str | None = typer.Option(None, "--once", help="One-off time, e.g. 'tomorrow 9:30' or 'in 3 days'."),
    every: str | None = typer.Option(None, "--every", help="Repeating interval, e.g. '15 minutes' or '2 hours'."),
    at: str | None = typer.Option(None, "--at", help="Time of day for a daily job, e.g. '09:30'."),
    instruction: str = typer.Option("", "--instruction", "-i", help="What Iris should do when the job fires."),
) -> None:
    """Time-triggered work: list, add, rm."""
    cron_mod = _load("cron")
    raise typer.Exit(
        code=cron_mod.run(action, job_id, once=once, every=every, at=at, instruction=instruction)
    )


@app.command()
def tools(
    action: str = typer.Argument("policy", help="policy (default) | actions"),
    limit: int = typer.Option(20, "--limit", "-n", help="How many actions to list."),
) -> None:
    """Inspect the tool surface and its policy (read-only): policy, actions."""
    tools_mod = _load("tools")
    raise typer.Exit(code=tools_mod.run(action, limit=limit))


@app.command()
def plugins(
    action: str = typer.Argument("channels", help="channels | tools | hooks | mcp | add"),
    target: str = typer.Argument("", help="For add: a plugin directory."),
    live: bool = typer.Option(
        False, "--live", help="For mcp: connect the declared servers and list their tools."
    ),
) -> None:
    """Inspect registered capabilities, or install an Agent Plugin."""
    plugins_mod = _load("plugins")
    raise typer.Exit(code=plugins_mod.run(action, target=target, live=live))


@app.command()
def policy(action: str = typer.Argument("show", help="show (default) | classes | overrides | servers")) -> None:
    """Show what every tool and server is allowed to do, and where that came from."""
    policy_mod = _load("policy")
    raise typer.Exit(code=policy_mod.run(action))


@app.command()
def mcp(
    action: str = typer.Argument("list", help="list | add | remove | test"),
    name: str = typer.Argument("", help="Server name."),
    url: str = typer.Option("", "--url", help="For add: an http/sse server's URL."),
    command: str = typer.Option("", "--command", help="For add: a stdio server's command."),
    args: str = typer.Option("", "--args", help="For add: comma-separated arguments."),
    trust: str = typer.Option("untrusted", "--trust", help="untrusted | review | owner"),
    approval: str = typer.Option("auto", "--approval", help="auto | always | never"),
    enabled: bool = typer.Option(True, "--enabled/--disabled", help="Declare it now or park it."),
    transport: str = typer.Option("", "--transport", help="stdio | http | sse"),
) -> None:
    """Declare MCP servers (list, add, remove, test) without hand-editing JSON."""
    mcp_mod = _load("mcp")
    raise typer.Exit(
        code=mcp_mod.run(
            action,
            name=name,
            url=url,
            command=command,
            args=args,
            trust=trust,
            approval=approval,
            enabled=enabled,
            transport=transport,
        )
    )


@app.command()
def costs(
    action: str = typer.Argument("summary", help="summary | daily | weekly"),
    days: int = typer.Option(14, "--days", "-n", help="How many days `daily` reports."),
) -> None:
    """What the model calls actually cost, from the append-only ledger."""
    costs_mod = _load("costs")
    raise typer.Exit(code=costs_mod.run(action, days=days))


@app.command("eval")
def eval_command(
    kind: str = typer.Argument("", help="context | memory | persona | capture | consolidator | engine"),
    suite: str = typer.Option("", "--suite", help="Suite name. Empty runs every suite for the kind."),
    component: str = typer.Option("default", "--component", help="Component to score."),
    json_output: bool = typer.Option(False, "--json", help="Print the scores as JSON."),
    live: bool = typer.Option(False, "--live", help="Score with the configured model. Refused for staged code."),
) -> None:
    """Score a component offline. No arguments prints the shipped suites."""
    eval_mod = _load("eval_cmd")
    raise typer.Exit(code=eval_mod.run(kind, suite=suite, component=component, json_output=json_output, live=live))


@app.command()
def evolve(
    kind: str = typer.Argument(..., help="context | memory | persona | capture | consolidator"),
    suite: str = typer.Option("temporal-recall", "--suite", help="Suite to search."),
    iterations: int = typer.Option(2, "--iterations", help="How many proposer rounds."),
    budget_usd: float = typer.Option(1.0, "--budget-usd", help="Stop when the ledger would exceed this."),
    allow_audit_isolation: bool = typer.Option(
        False, "--allow-audit-isolation", help="Run where the jail is audit-only. WSL2 is the alternative."
    ),
) -> None:
    """Search a suite. The kernel scores. Nothing is activated."""
    from pathlib import Path

    from iris_ai.config import settings
    from iris_ai.evolve.run import run_evolve

    def proposer(index: int) -> list[dict[str, str]]:
        if index:
            return []
        names = {
            "context": "temporal-rag",
            "memory": "evidence-memory",
            "capture": "decision-only",
            "consolidator": "conflict-resolver",
            "persona": "strict-reviewer",
        }
        return [{"name": names.get(kind, kind), "source": f"class {kind}:\n    pass\n", "notes": "shipped candidate"}]

    try:
        report = run_evolve(
            kind,
            suite,
            root=Path(settings.workspace_dir),
            proposer=proposer,
            iterations=iterations,
            budget_usd=budget_usd,
            allow_audit=allow_audit_isolation,
        )
    except PermissionError as exc:
        console().print(str(exc))
        raise typer.Exit(code=1) from exc
    frontier = ", ".join(row["name"] for row in report["frontier"]) or "(empty)"
    console().print(f"frontier: {frontier}")
    console().print("activated: no")
    raise typer.Exit(code=0)


@app.command()
def memory(
    action: str = typer.Argument("conflicts", help="conflicts"),
    conflict_id: str = typer.Argument("", help="Conflict id, with --resolve."),
    resolve: str = typer.Option("", "--resolve", help="keep | replace | both"),
) -> None:
    """List open memory conflicts, or resolve one."""
    from pathlib import Path

    from iris_ai.config import settings
    from iris_ai.memory.conflicts import list_conflicts, resolve_conflict

    root = Path(settings.workspace_dir)
    if resolve:
        if resolve not in {"keep", "replace", "both"} or not conflict_id:
            console().print("usage: iris memory conflicts ID --resolve keep|replace|both")
            raise typer.Exit(code=2)
        try:
            resolve_conflict(root, conflict_id, resolve)
        except KeyError:
            console().print(f"no open conflict {conflict_id}")
            raise typer.Exit(code=1) from None
        console().print(f"resolved {conflict_id} {resolve}")
        raise typer.Exit(code=0)
    rows = list_conflicts(root)
    if not rows:
        console().print("no open conflicts")
        raise typer.Exit(code=0)
    for row in rows:
        console().print(f"{row.get('id')}  {row.get('incoming', '')[:80]}")
    raise typer.Exit(code=0)


@app.command()
def trace(
    session: str = typer.Option("", "--session", "-s", help="Only turns from this session."),
    last: int = typer.Option(1, "--last", "-n", help="How many turns to show."),
    json_output: bool = typer.Option(False, "--json", help="Print the trace records as JSON."),
) -> None:
    """Explain the last turn: model, components, tools, tokens, and cost."""
    trace_mod = _load("trace_cmd")
    raise typer.Exit(code=trace_mod.run(session=session, last=last, json_output=json_output))


@app.command()
def secrets(
    action: str = typer.Argument("backend", help="backend | list | set | rm"),
    name: str = typer.Argument("", help="Secret name, e.g. the ${VAR} a server uses."),
    value: str = typer.Argument("", help="Value (omit to be prompted, hidden)."),
) -> None:
    """Where secrets live, which are missing, and store or remove one (names only, never values)."""
    secrets_mod = _load("secrets")
    raise typer.Exit(code=secrets_mod.run(action, name=name, value=value))


@app.command()
def init(
    config: Path | None = typer.Option(
        None, "--config", help="Where to write the harness manifest (default: HARNESS_CONFIG)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing .env or manifest."),
    offline: bool = typer.Option(False, "--offline", help="Skip the model and embedding probes."),
    yes: bool = typer.Option(False, "--yes", help="Write a blank identity and skip the setup screen."),
    provider: str = typer.Option("", "--provider", help="Provider name. Skips the questions."),
    model: str = typer.Option("", "--model", help="Model id. The provider prefix is added if missing."),
    api_key_env: str = typer.Option(
        "", "--api-key-env", help="Env var that already holds the key. The value is not printed."
    ),
    memory_mode: str = typer.Option(
        "", "--memory", help="none for keyword memory, semantic for embeddings."
    ),
) -> None:
    """Set up this checkout, then prove it: config, model check, memory, recall."""
    init_mod = _load("init")
    raise typer.Exit(
        code=init_mod.run(
            config=config,
            force=force,
            offline=offline,
            yes=yes,
            provider=provider,
            model=model,
            api_key_env=api_key_env,
            memory_mode=memory_mode,
        )
    )


@app.command()
def config(
    section: str = typer.Argument("", help="provider | key | model | embeddings | parts | mcp | profile"),
) -> None:
    """Open setup again. Pass a section to start there."""
    setup_mod = _load("setup")
    raise typer.Exit(code=setup_mod.run_config(yes=False, section=section))


@app.command()
def models(
    action: str = typer.Argument("test", help="test"),
    model: str = typer.Argument("", help="Optional model id to probe."),
) -> None:
    """Probe the configured model with one short call."""
    models_mod = _load("models")
    raise typer.Exit(code=models_mod.run(action, model))


@app.command()
def components(
    action: str = typer.Argument("list", help="list | use | eject | check | rollback | add | simulate | lock"),
    kind: str = typer.Argument("", help="context | memory | persona | capture | consolidator | channel"),
    option: str = typer.Argument("", help="The option to switch to."),
    live: bool = typer.Option(False, "--live", help="Score with the configured model. Refused for staged code."),
    staged: bool = typer.Option(False, "--staged", help="The component is not approved yet."),
    yes: bool = typer.Option(False, "--yes", help="Confirm an install without a prompt."),
    as_plugin: str = typer.Option("", "--as-plugin", help="Write an Agent Plugin into this directory."),
) -> None:
    """List plug-and-play options, or switch one."""
    mod = _load("components_cmd")
    raise typer.Exit(code=mod.run(action, kind, option, live=live, staged=staged, yes=yes, as_plugin=as_plugin))


@app.command()
def serve(
    channel: str = typer.Argument("terminal", help="terminal | telegram | http"),
    host: str = typer.Option("127.0.0.1", "--host", help="HTTP bind address."),
    port: int = typer.Option(8000, "--port", help="HTTP port."),
    insecure: bool = typer.Option(False, "--insecure", help="Allow a non-loopback HTTP bind without IRIS_API_TOKEN."),
) -> None:
    """Start a face: the terminal, Telegram, or the HTTP API."""
    serve_mod = _load("serve")
    raise typer.Exit(code=serve_mod.run(channel, host=host, port=port, insecure=insecure))


@app.command("new")
def new(
    kind: str = typer.Argument(..., help="context | memory | persona | capture | consolidator | role | component"),
    name: str = typer.Argument("", help="Name, or context|memory when kind is component."),
    dest: Path = typer.Option(Path("examples"), "--dest", help="Directory to write the scaffold into."),
) -> None:
    """Scaffold a folder component, or print the entry-point recipe for other kinds."""
    scaffold_mod = _load("scaffold")
    from iris_ai.plug import FOLDER_KINDS, entry_point_hint

    if kind not in {*FOLDER_KINDS, "role", "component"}:
        console().print(entry_point_hint(kind))
        return
    if kind == "component":
        if name not in {"context", "memory"}:
            _fail("usage: iris new component context|memory")
            return
        path = scaffold_mod.write_component(name, dest / name / "component.py")
        console().print(f"wrote {path}")
        return
    if kind == "role":
        if not name:
            _fail("usage: iris new role <name>")
            return
        path = scaffold_mod.write_new(kind, name, dest)
        console().print(f"wrote {path}")
        return
    if kind not in FOLDER_KINDS or not name:
        _fail("usage: iris new context|memory|persona|capture|consolidator|role <name>")
        return
    from iris_ai.plug import scaffold

    folder = scaffold(kind, name, root=dest if str(dest) != "examples" else None)
    console().print(f"wrote {folder}")
    console().print(f"check it with `iris components check {kind} {name}`, then `iris components use {kind} {name}`")


@app.command()
def bench(
    which: str = typer.Argument("harness", help="Which benchmark to run. Only harness exists."),
) -> None:
    """Run the scripted harness benchmark. No live model."""
    bench_mod = _load("bench_cmd")
    raise typer.Exit(code=bench_mod.run(which))


@app.command()
def core(
    action: str = typer.Argument(..., help="propose"),
    request: str = typer.Argument("", help="What the proposal should say."),
) -> None:
    """Propose a core change in a git worktree. Never applies it."""
    core_mod = _load("core_cmd")
    raise typer.Exit(code=core_mod.run(action, request))


@app.command()
def migrate(
    to: str = typer.Option("sqlite", "--to", help="Memory backend to move to (sqlite | pgvector)."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report without writing anything."),
) -> None:
    """Move memory to another store: rebuild the index from the Markdown."""
    migrate_mod = _load("migrate")
    raise typer.Exit(code=migrate_mod.run(to=to, dry_run=dry_run))


@app.command()
def guards(json_output: bool = typer.Option(False, "--json", help="Machine-readable snapshot.")) -> None:
    """The guard chain and today's token budget (read-only, no engine needed)."""
    guards_mod = _load("guards")
    raise typer.Exit(code=guards_mod.run(json_output=json_output))


@app.command()
def version() -> None:
    """Print version, Python, and install location."""
    version_mod.print_version()


@app.command()
def doctor(
    fix: bool = typer.Option(False, "--fix", help="Apply the safe fixes (create a missing .env)."),
) -> None:
    """Offline environment checks (names only — never secret values)."""
    doctor_mod = _load("doctor")
    try:
        if fix:
            for note in doctor_mod.apply_safe_fixes():
                console().print(note)
        checks = doctor_mod.run_checks()
    except Exception as exc:  # noqa: BLE001 — doctor must survive any crash and report it
        _fail(f"doctor crashed: {exc}", _debug_flag, cause=exc)
        return
    doctor_mod.render(checks)
    raise typer.Exit(code=doctor_mod.exit_code(checks))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
