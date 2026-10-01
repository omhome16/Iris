"""The questions `iris init` asks, independent of how they are drawn."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime

from iris_ai.cli.ask import Prompter
from iris_ai.providers import AUTO_ORDER, PROVIDERS, qualify


@dataclass
class SetupPlan:
    provider: str = "auto"
    api_key: str = ""
    model: str = ""
    embeddings: str = "none"
    owner_name: str = ""
    assistant_name: str = "assistant"
    tone: str = ""
    timezone: str = "UTC"
    mcp: tuple[str, ...] = ()
    extras: tuple[str, ...] = field(default_factory=tuple)


def _key_present(name: str) -> bool:
    spec = PROVIDERS.get(name)
    if spec is None or not spec.key_env:
        return name == "ollama"
    if os.environ.get(spec.key_env, "").strip():
        return True
    env = _dotenv()
    return bool(env.get(spec.key_env, "").strip())


def _dotenv() -> dict[str, str]:
    from pathlib import Path

    path = Path(".env")
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _detected_tz() -> str:
    tz = datetime.now().astimezone().tzinfo
    key = getattr(tz, "key", None)
    return str(key) if key else "UTC"


def collect(prompter: Prompter, *, section: str = "", skip_verify: bool = False) -> SetupPlan | None:
    """Walk the setup. Returns None when the owner declines the summary."""
    plan = SetupPlan(timezone=_detected_tz())
    steps = ["provider", "key", "model", "memory", "profile", "extras", "summary"]
    if section and section not in steps:
        section = ""
    wanted = {section} if section else set(steps)

    if "provider" in wanted:
        prompter.say("Which service should answer? A key you already saved is marked.")
        choices = []
        default = "ollama"
        for name in (*AUTO_ORDER, "ollama", "any"):
            spec = PROVIDERS[name]
            tag = "local" if name == "ollama" else ("key found" if _key_present(name) else "needs a key")
            choices.append((name, f"{spec.label}  ({tag})"))
            if _key_present(name) and default == "ollama" and name != "ollama":
                default = name
        plan.provider = prompter.select("Provider", choices, default=default)

    spec = PROVIDERS.get(plan.provider)
    if "key" in wanted and spec is not None and spec.key_env:
        if _key_present(plan.provider):
            prompter.say(f"A {spec.key_env} is already saved. It stays hidden.")
            if not prompter.confirm("Use the saved key?", default=True):
                plan.api_key = prompter.secret(f"Paste a new {spec.key_env}")
        else:
            prompter.say(f"Get a key at {spec.docs or 'the provider site'}. It is stored in .env and never shown.")
            plan.api_key = prompter.secret(spec.key_env)

    if "model" in wanted and spec is not None:
        prompter.say("Pick a model. The recommended one is already selected. Other lets you type an id.")
        current = qualify(plan.provider, getattr(__import__("iris_ai.config", fromlist=["settings"]).settings, spec.strong_field, "") or "")
        choices = [(current or "recommended", f"{current or 'provider default'}  (recommended)")]
        choices.append(("other", "Other — type a model id"))
        picked = prompter.select("Model", choices, default=choices[0][0])
        if picked == "other":
            typed = prompter.text("Model id", default="")
            plan.model = qualify(plan.provider, typed) if typed else current
        else:
            plan.model = current
        if not skip_verify and (plan.api_key or _key_present(plan.provider)):
            from iris_ai.setup.verify import verify_model

            ok, detail = verify_model(plan.provider, plan.model, plan.api_key)
            prompter.say(detail)
            if not ok and not prompter.confirm("Save this anyway?", default=False):
                return None

    if "memory" in wanted:
        prompter.say("Keyword memory needs nothing else. Semantic memory needs Gemini or a local Ollama embedder.")
        plan.embeddings = prompter.select(
            "Memory",
            [
                ("none", "Keyword memory (no extra setup)"),
                ("semantic", "Semantic memory (Gemini, or Ollama if you run it)"),
            ],
            default="none",
        )

    if "profile" in wanted:
        prompter.say("What should the assistant call you, and what should you call it?")
        plan.owner_name = prompter.text("Your name", default="")
        plan.assistant_name = prompter.text("Assistant name", default="assistant")
        plan.timezone = prompter.text("Timezone", default=plan.timezone)
        plan.tone = prompter.select(
            "Tone",
            [("plain", "Plain"), ("warm", "Warm"), ("brief", "Brief")],
            default="plain",
        )

    if "extras" in wanted:
        prompter.say("Everything here is off unless you opt in. You can add them later.")
        from iris_ai.mcp.catalog import load_catalog

        choices = [(name, f"MCP: {name}") for name in list(load_catalog())[:8]]
        choices.append(("typesafe", "TypeSafe JEV key (optional judgments)"))
        choices.append(("keyring", "OS keychain for secrets"))
        picked = prompter.checkbox("Extras", choices)
        plan.mcp = tuple(name for name in picked if name not in {"typesafe", "keyring"})
        plan.extras = tuple(name for name in picked if name in {"typesafe", "keyring"})

    if "summary" in wanted:
        spec = PROVIDERS.get(plan.provider)
        key_name = spec.key_env if spec is not None else ""
        prompter.say(
            "\n".join(
                [
                    "This will write:",
                    f"  provider: {plan.provider}",
                    f"  model: {plan.model or '(provider default)'}",
                    f"  key: {key_name or '(none)'} (value hidden)",
                    f"  memory: {'keyword' if plan.embeddings == 'none' else 'semantic'}",
                    f"  assistant: {plan.assistant_name}",
                    "  files: .env, config/harness.toml, workspace/",
                ]
            )
        )
        if not prompter.confirm("Write this setup?", default=True):
            return None
    return plan


def apply_plan(plan: SetupPlan, *, root: str = "") -> None:
    """Write the plan, then reload settings so the same process sees it."""
    from pathlib import Path

    from iris_ai.cli.setup import apply_wizard
    from iris_ai.config import reload, settings

    workspace = Path(root or settings.workspace_dir)
    apply_wizard(
        workspace,
        provider=plan.provider,
        api_key=plan.api_key,
        model=plan.model,
        embeddings="none" if plan.embeddings == "none" else "semantic",
        owner_name=plan.owner_name,
        assistant_name=plan.assistant_name,
        tone=plan.tone,
        timezone=plan.timezone,
        mcp=plan.mcp,
    )
    reload()
