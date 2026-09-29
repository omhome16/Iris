"""`iris secrets` — where a token lives, and which ones are still missing.

Two jobs, and the second is the one that saves an afternoon:

- **Store** a secret in the configured backend (`set`, `rm`), so it does not have
  to be exported in a shell or written into `.env`.
- **Diagnose** — every `${VAR}` a declared MCP server references, resolved or
  missing, and *where* each one was found. "The server says 401" and "your token
  is not where you think it is" are the same bug from two ends.

Nothing here ever prints a value: names, locations and `set`/`missing`. That is
the same rule `iris doctor` follows, and it is why `list` can be shown to someone.
"""

from __future__ import annotations

import typer
from rich.table import Table

from iris_ai.cli.help_theme import console
from iris_ai.config import settings


def _backend() -> int:
    """Which store is in use, where it is, and which ones this machine has."""
    out = console()
    from iris_ai.secrets import BACKENDS, SecretStoreError, available_backends, resolve_store

    try:
        store = resolve_store()
    except SecretStoreError as exc:
        out.print(f"[iris.fail]no usable secret store:[/iris.fail] {exc}")
        return 1

    table = Table(title="Secret store", title_style="iris.title", header_style="iris.title")
    table.add_column("backend")
    table.add_column("available")
    for name, ok in available_backends().items():
        table.add_row(name, "yes" if ok else "no")
    out.print(table)
    out.print(f"active: [iris.ok]{store.name}[/iris.ok] — {store.location()}")
    out.print(f"[dim]chosen by SECRET_STORE={settings.secret_store!r} ({', '.join(BACKENDS)})[/dim]")
    out.print(f"[dim]file for the `file` backend: {settings.secrets_file}[/dim]")
    if store.name == "file":
        out.print(
            "[iris.warn]the file backend is not encrypted — for OS-level encryption install "
            "the extra: pip install 'iris-personal-ai[secrets]'[/iris.warn]"
        )
    return 0


def _referenced() -> list[str]:
    """Every `${VAR}` the declaration file mentions, in the order it mentions them.

    Read as *text*, not through `load_servers`: that path resolves each
    placeholder and refuses a declaration whose secret is missing — which is
    exactly the case this command exists to diagnose. A file that does not parse
    is still scanned, because the names it references are the useful answer even
    then (and `iris plugins mcp` is where the parse error is reported).
    """
    import re
    from pathlib import Path

    path = Path(settings.mcp_servers_file)
    if not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:  # unreadable is worth saying, and not a crash
        console().print(f"[iris.warn]could not read {path}:[/iris.warn] {exc}")
        return []
    names: list[str] = []
    for name in re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", text):
        if name not in names:
            names.append(name)
    return names


def _list() -> int:
    out = console()
    from iris_ai.secrets import SecretStoreError, lookup, resolve_store

    names = _referenced()
    if not names:
        out.print("[dim]No declared MCP server references a ${VAR} secret.[/dim]")
        _backend_names_only(out)
        return 0

    table = Table(title="Secrets referenced", title_style="iris.title", header_style="iris.title")
    table.add_column("name")
    table.add_column("found in")
    missing = 0
    for name in names:
        try:
            value, where = lookup(name)
        except SecretStoreError as exc:
            out.print(f"[iris.fail]secret store unusable:[/iris.fail] {exc}")
            return 1
        if value is None:
            missing += 1
            table.add_row(name, "[iris.fail]missing[/iris.fail]")
        else:
            table.add_row(name, f"[iris.ok]{where}[/iris.ok]")
    out.print(table)
    if missing:
        out.print(
            f"[dim]{missing} missing — `iris secrets set <NAME>` stores one in "
            f"{resolve_store().name}; an environment variable is found first.[/dim]"
        )
    return 0


def _backend_names_only(out) -> None:
    """The useful line when there is nothing declared: how to add one."""
    out.print("[dim]add one: `iris secrets set NAME` (stored in the active backend).[/dim]")


def _set(name: str, value: str) -> int:
    out = console()
    from iris_ai.secrets import SecretStoreError, resolve_store

    if not value:
        # Prompted, not echoed: a value in a shell's history is a value leaked.
        value = typer.prompt(f"value for {name}", hide_input=True)
    if not value:
        out.print("[iris.fail]nothing to store (empty value)[/iris.fail]")
        return 1
    try:
        store = resolve_store()
        store.set(name, value)
    except SecretStoreError as exc:
        out.print(f"[iris.fail]could not store {name}:[/iris.fail] {exc}")
        return 1
    out.print(f"[iris.ok]stored[/iris.ok] {name} in {store.name}")
    out.print(f"[dim]{store.location()}[/dim]")
    return 0


def _rm(name: str) -> int:
    out = console()
    from iris_ai.secrets import SecretStoreError, resolve_store

    try:
        store = resolve_store()
        removed = store.delete(name)
    except SecretStoreError as exc:
        out.print(f"[iris.fail]could not remove {name}:[/iris.fail] {exc}")
        return 1
    if not removed:
        out.print(f"[iris.warn]{name} was not in {store.name}[/iris.warn]")
        return 1
    out.print(f"[iris.ok]removed[/iris.ok] {name} from {store.name}")
    return 0


def run(action: str = "backend", name: str = "", value: str = "") -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    try:
        match action:
            case "backend" | "status":
                return _backend()
            case "list":
                return _list()
            case "set":
                if not name:
                    out.print("[iris.fail]`set` needs a name[/iris.fail]")
                    return 2
                return _set(name, value)
            case "rm" | "delete":
                if not name:
                    out.print("[iris.fail]`rm` needs a name[/iris.fail]")
                    return 2
                return _rm(name)
            case _:
                out.print(
                    f"[iris.fail]unknown action {action!r}[/iris.fail] — use backend, list, set or rm"
                )
                return 2
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - an inspection command must not traceback
        out.print(f"[iris.fail]secret store error:[/iris.fail] {exc}")
        return 1
