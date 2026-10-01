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

from iris_ai.cli import ui
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
    transport: str = "",
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
    if transport:
        entry["transport"] = transport
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
    ui.note(out, f"trust={trust} approval={approval} enabled={str(enabled).lower()}")
    if trust == "untrusted":
        ui.note(
            out,
            "untrusted (the default): its writes are denied and its output is screened. "
            "`iris mcp add ... --trust owner` only for a server you wrote.",
        )
    ui.note(out, f"verify with `iris mcp test {name}`")
    return 0


def _remove(name: str) -> int:
    out = console()
    try:
        data = _read()
    except (OSError, ValueError) as exc:
        ui.failed(out, f"could not read {_path()}:", str(exc))
        return 1
    if name not in data["mcpServers"]:
        ui.warn(out, f"{name} is not declared in {_path()}")
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
        ui.warn(out, f"{name} is not declared in {_path()}")
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
        ui.failed(out, f"{name} did not connect:", str(row["error"]))
        if row.get("retryable"):
            ui.note(out, "a boot would keep retrying this one in the background.")
        return 1

    ui.header(
        out,
        f"{name} - {row['transport']}",
        f"connected: {len(row['tools'])} tool(s), and the policy each one would get",
    )
    table = ui.table(out, "", ["tool", "read-only", "policy"])
    style = {"allow": "iris.ok", "ask": "iris.warn", "deny": "iris.fail"}
    for tool in row["tools"]:
        table.add_row(
            namespaced(name, tool["tool"].split("/", 1)[-1]),
            "yes" if tool["read_only"] else "no",
            f"[{style[tool['policy']]}]{tool['policy']}[/{style[tool['policy']]}]",
        )
    out.print(table)
    ui.note(out, "the policy shown is what the surface will apply.")
    return 0


def _add_preset(name: str) -> int:
    """Write a catalog server. Missing env keys are named, never printed."""
    import os

    from iris_ai.mcp import McpConfigError, validate_entry
    from iris_ai.mcp.catalog import preset

    out = console()
    body = preset(name)
    if body is None:
        ui.error(out, f"no catalog preset {name!r}")
        return 2
    needed = [str(key) for key in body.get("env") or []]
    missing = [key for key in needed if not os.environ.get(key, "").strip()]
    entry: dict = {
        "command": body.get("command") or "",
        "args": list(body.get("args") or []),
        "transport": body.get("transport") or "stdio",
        "trust": body.get("trust") or "untrusted",
        "approval": "auto",
        "enabled": not missing,
    }
    if needed:
        entry["env"] = {key: "${" + key + "}" for key in needed}
    try:
        validate_entry(name, entry, environ=os.environ)
    except McpConfigError as exc:
        out.print(f"[iris.fail]that declaration is not valid:[/iris.fail] {exc}")
        return 1
    data = _read()
    data["mcpServers"][name] = entry
    _write(data)
    out.print(f"[iris.ok]added[/iris.ok] {name} from the catalog")
    if missing:
        ui.note(out, "set " + ", ".join(missing) + f" then `iris mcp enable {name}`")
    return 0


def _set_enabled(name: str, enabled: bool) -> int:
    out = console()
    data = _read()
    if name not in data["mcpServers"]:
        ui.warn(out, f"{name} is not declared in {_path()}")
        return 1
    data["mcpServers"][name]["enabled"] = enabled
    _write(data)
    state = "enabled" if enabled else "disabled"
    out.print(f"[iris.ok]{state}[/iris.ok] {name}")
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
                    ui.error(out, "`add` needs a name")
                    return 2
                from iris_ai.mcp.catalog import preset

                if preset(name) and not options.get("url") and not options.get("command"):
                    return _add_preset(name)
                return _add(name, **options)
            case "enable":
                return _set_enabled(name, True)
            case "disable":
                return _set_enabled(name, False)
            case "remove" | "rm":
                if not name:
                    ui.error(out, "`remove` needs a name")
                    return 2
                return _remove(name)
            case "test":
                if not name:
                    ui.error(out, "`test` needs a name")
                    return 2
                return _test(name)
            case _:
                ui.failed(out, f"unknown action {action!r}", "- use list, add, remove, test, enable or disable")
                return 2
    except typer.Exit:
        raise
    except Exception as exc:  # noqa: BLE001 - a declaration command must not traceback
        ui.failed(out, "mcp error:", str(exc))
        return 1
