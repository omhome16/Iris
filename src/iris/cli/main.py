"""Iris CLI root — typer app bound to [project.scripts] iris."""

from __future__ import annotations

import os

import typer

from iris.cli import agents as agents_mod
from iris.cli import art
from iris.cli import chat as chat_mod
from iris.cli import cron as cron_mod
from iris.cli import doctor as doctor_mod
from iris.cli import guards as guards_mod
from iris.cli import skills as skills_mod
from iris.cli import tools as tools_mod
from iris.cli import version as version_mod
from iris.cli.help_theme import console

app = typer.Typer(
    name="iris",
    help="Iris — personal agent harness (library + CLI).",
    invoke_without_command=True,
    add_completion=False,
    rich_markup_mode="rich",
    context_settings={"help_option_names": ["-h", "--help"]},
)

# Written by the root callback; read by commands that need --debug state.
# click only guarantees root params exist during callback execution, so we
# stash the resolved flag instead of reading it lazily inside subcommands.
_debug_flag = False


def _debug_enabled(debug: bool) -> bool:
    return debug or os.environ.get("IRIS_DEBUG", "") == "1"


def _fail(message: str, debug: bool, *, cause: BaseException | None = None) -> None:
    """Print a friendly error; under debug re-raise `cause` for a traceback."""
    out = console()
    out.print(f"[iris.fail]error[/iris.fail] {message}")
    if debug:
        if cause is not None:
            raise cause  # original traceback (spec: --debug / IRIS_DEBUG=1)
        raise typer.Exit(code=1)
    out.print("hint: re-run with --debug or IRIS_DEBUG=1 for a traceback")
    raise typer.Exit(code=1)


@app.callback()
def root(
    ctx: typer.Context,
    version: bool = typer.Option(
        False, "--version", "-V", help="Show version and exit.", is_eager=True
    ),
    debug: bool = typer.Option(False, "--debug", help="Full tracebacks on error."),
) -> None:
    global _debug_flag
    _debug_flag = _debug_enabled(debug)
    if version:
        version_mod.print_version()
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        intro(ctx)
        raise typer.Exit()


def intro(ctx: typer.Context) -> None:
    """The start screen: the mark, then the real help.

    Drawing is skipped in a pipe, under `NO_COLOR`, or with `IRIS_NO_BANNER=1`
    (`iris.cli.art.banner_enabled`), so scripts and CI logs get the same text
    they always did. The help that follows is the honest reference — the banner
    is decoration, never a substitute for the command list.
    """
    out = console()
    if art.banner_enabled(out):
        art.render_banner(out, subtitle=version_mod.version_lines()[0])
        out.print("  [dim]memory · judgment · agents — `iris chat` to talk to her[/dim]\n")
    print(ctx.get_help())


@app.command()
def chat(
    session: str = typer.Option("cli", "--session", help="Conversation thread id (memory continuity)."),
    once: str | None = typer.Option(None, "--once", help="Run a single turn and exit."),
    no_banner: bool = typer.Option(False, "--no-banner", help="Skip the start-screen art."),
) -> None:
    """Chat with Iris in the terminal (streaming; same pipeline as the API)."""
    raise typer.Exit(
        code=chat_mod.run_chat(session=session, once=once, debug=_debug_flag, no_banner=no_banner)
    )


@app.command()
def skills(
    action: str = typer.Argument("list", help="list | show <name> | validate"),
    name: str | None = typer.Argument(None, help="Skill name (for `show`)."),
) -> None:
    """Inspect the skill registry (read-only): list, show, validate."""
    raise typer.Exit(code=skills_mod.run(action=action, name=name))


@app.command()
def agents(
    action: str = typer.Argument("roles", help="roles | show <name> | handoffs"),
    name: str | None = typer.Argument(None, help="Role name (for `show`)."),
    limit: int = typer.Option(10, "--limit", "-n", help="How many decisions to list."),
) -> None:
    """Inspect the multi-agent layer (read-only): roles, show, handoffs."""
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
    raise typer.Exit(
        code=cron_mod.run(action, job_id, once=once, every=every, at=at, instruction=instruction)
    )


@app.command()
def tools(
    action: str = typer.Argument("policy", help="policy (default) | actions"),
    limit: int = typer.Option(20, "--limit", "-n", help="How many actions to list."),
) -> None:
    """Inspect the tool surface and its policy (read-only): policy, actions."""
    raise typer.Exit(code=tools_mod.run(action, limit=limit))


@app.command()
def guards(json_output: bool = typer.Option(False, "--json", help="Machine-readable snapshot.")) -> None:
    """The guard chain and today's token budget (read-only, no engine needed)."""
    raise typer.Exit(code=guards_mod.run(json_output=json_output))


@app.command()
def version() -> None:
    """Print version, Python, and install location."""
    version_mod.print_version()


@app.command()
def doctor() -> None:
    """Offline environment checks (names only — never secret values)."""
    try:
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
