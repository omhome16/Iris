"""`iris mcp` — the verbs for declaring a server, so `.mcp.json` is not the only way.

`iris plugins mcp` *inspects*; this *edits*. Both exist because they answer
different questions at different moments: adding a server should not require
knowing the file's shape, and inspecting one should not risk changing it.

- **`list`** — the declared servers, one line each (delegates to the same readout
  as `iris plugins mcp`, because a second implementation of the same question is
  a second answer waiting to disagree).
- **`add <name> --url ... | --command ...`** — writes one server into the
  declaration file, preserving everything already in it, and validates the result
  through the same parser the boot uses before saving. A written file that the
  loader would reject is worse than no edit at all.
- **`remove <name>`** — deletes one entry, and says whether it was there.
- **`test <name>`** — connects to *that* server and reports its tools, without
  connecting the rest. The narrow question ("is this one server working?") is the
  one worth answering quickly.

Trust is not set implicitly: `add` takes `--trust` and defaults to `untrusted`,
which denies a non-read-only tool until the owner says otherwise. A CLI that
defaulted to `owner` would be handing away the trust model for convenience.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer
from rich.table import Table

from iris_ai.cli.help_theme import console
from iris_ai.config import settings


def _path() -> Path:
    return Path(settings.mcp_servers_file)


def _read() -> dict:
    """The declaration file as a dict, or an empty map when there is none."""
    path = _path()
    if not path.is_file():
        return {"mcpServers": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
        raise ValueError(f"{path}: expected an `mcpServers` object")
    data.setdefault("mcpServers", {})
    return data


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _list() -> int:
    from iris_ai.cli import plugins as plugins_mod

    return plugins_mod.run("mcp")


def _add(
    name: str,
    *,
    url: str = "",
    command: str = "",
    args: str = "",
    trust: str = "untrusted",
    approval: str = "auto",
    enabled: bool = True,
) -> int:
    out = console()
    from iris_ai.mcp import McpConfigError, validate_entry

    if not name or "/" in name:
        out.print("[iris.fail]a server name is required (and may not contain '/')[/iris.fail]")
        return 2
    if bool(url) == bool(command):
        out.print("[iris.fail]give exactly one of --url or --command[/iris.fail]")
        return 2

    entry: dict = {"url": url} if url else {"command": command}
    if command and args:
        entry["args"] = [a for a in args.split(",") if a]
    entry["trust"] = trust
    entry["approval"] = approval
    entry["enabled"] = enabled

    try:
        # The same parser the boot uses, so a file this command writes is a file
        # the engine will accept. An invalid edit is refused before it is saved.
        validate_entry(name, entry, environ={})
    except McpConfigError as exc:
        out.print(f"[iris.fail]that declaration is not valid:[/iris.fail] {exc}")
        return 1

    try:
        data = _read()
    except (OSError, ValueError) as exc:
        out.print(f"[iris.fail]could not read {_path()}:[/iris.fail] {exc}")
        return 1

    existed = name in data["mcpServers"]
    data["mcpServers"][name] = entry
    _write(data)
    verb = "updated" if existed else "added"
    out.print(f"[iris.ok]{verb}[/iris.ok] {name} in {_path()}")
    out.print(f"[dim]trust={trust} approval={approval} enabled={str(enabled).lower()}[/dim]")
    if trust == "untrusted":
        out.print(
            "[dim]untrusted (the default): its writes are denied and its output is screened. "
            "`iris mcp add ... --trust owner` only for a server you wrote.[/dim]"
        )
    out.print(f"[dim]verify with `iris mcp test {name}`[/dim]")
    return 0


def _remove(name: str) -> int:
    out = console()
    try:
        data = _read()
    except (OSError, ValueError) as exc:
        out.print(f"[iris.fail]could not read {_path()}:[/iris.fail] {exc}")
        return 1
    if name not in data["mcpServers"]:
        out.print(f"[iris.warn]{name} is not declared in {_path()}[/iris.warn]")
        return 1
    del data["mcpServers"][name]
    _write(data)
    out.print(f"[iris.ok]removed[/iris.ok] {name} from {_path()}")
    return 0


def _test(name: str) -> int:
    """Connect one declared server and report what it offers."""
    out = console()
    from iris_ai.mcp import McpConfigError, load_servers, namespaced
    from iris_ai.mcp.provider import McpPool

    try:
        specs = [spec for spec in load_servers() if spec.name == name]
    except McpConfigError as exc:
        out.print(f"[iris.fail]MCP config is not usable:[/iris.fail] {exc}")
        return 1
    if not specs:
        out.print(f"[iris.warn]{name} is not declared in {_path()}[/iris.warn]")
        return 1

    async def _probe() -> list[dict]:
        async with McpPool(specs) as pool:
            return pool.status()

    try:
        (row,) = asyncio.run(_probe())
    except RuntimeError:
        out.print("[iris.fail]`iris mcp test` needs no running event loop[/iris.fail]")
        return 1

    if not row["connected"]:
        out.print(f"[iris.fail]{name} did not connect:[/iris.fail] {row['error']}")
        if row.get("retryable"):
            out.print("[dim]a boot would keep retrying this one in the background.[/dim]")
        return 1

    table = Table(title=f"{name} — {row['transport']}", title_style="iris.title", header_style="iris.title")
    table.add_column("tool")
    table.add_column("read-only")
    table.add_column("policy")
    style = {"allow": "iris.ok", "ask": "iris.warn", "deny": "iris.fail"}
    for tool in row["tools"]:
        table.add_row(
            namespaced(name, tool["tool"].split("/", 1)[-1]),
            "yes" if tool["read_only"] else "no",
            f"[{style[tool['policy']]}]{tool['policy']}[/{style[tool['policy']]}]",
        )
    out.print(table)
    out.print(f"[dim]{len(row['tools'])} tool(s); the policy shown is what the surface will apply.[/dim]")
    return 0


def run(action: str = "list", name: str = "", **options) -> int:
    """Entry point from the typer command. Returns the process exit code."""
    out = console()
    try:
        match action:
            case "list" | "ls":
                return _list()
            case "add":
                if not name:
                    out.print("[iris.fail]`add` needs a name[/iris.fail]")
                    return 2
                return _add(name, **options)
            case "remove" | "rm":
                if not name:
                    out.print("[iris.fail]`remove` needs a name[/iris.fail]")
                    return 2
                return _remove(name)
            case "test":
                if not name:
                    out.print("[iris.fail]`test` needs a name[/iris.fail]")
                    return 2
                return _test(name)
            case _:
                out.print(
                    f"[iris.fail]unknown action {action!r}[/iris.fail] — use list, add, remove or test"
                )
                return 2
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - a declaration command must not traceback
        out.print(f"[iris.fail]mcp error:[/iris.fail] {exc}")
        return 1
