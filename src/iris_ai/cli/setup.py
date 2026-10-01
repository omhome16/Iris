"""First-run setup and later edits. No persona until the owner writes one.

`iris init --yes` writes a neutral profile without questions, which is what CI
uses. `iris config` opens the same screen again.
"""

from __future__ import annotations

from pathlib import Path

from iris_ai.config import settings
from iris_ai.memory.files import WorkspaceFiles
from iris_ai.onboarding import OnboardingWizard


def apply_setup(
    root: Path,
    *,
    owner_name: str = "",
    assistant_name: str = "assistant",
    tone: str = "",
    timezone: str = "UTC",
    sleep_hour: str = "4",
    persona: str = "",
) -> None:
    files = WorkspaceFiles(root)
    OnboardingWizard(files, None).configure(
        owner_name=owner_name,
        assistant_name=assistant_name or "assistant",
        tone=tone,
        timezone=timezone or "UTC",
        sleep_hour=sleep_hour or "4",
        persona=persona,
    )


def write_env_key(path: Path, key: str, value: str) -> None:
    """Store one secret in `.env`. Callers must not print `value`."""
    if not key or not value:
        return
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    prefix = key + "="
    out = [prefix + value if line.startswith(prefix) else line for line in lines]
    if not any(line.startswith(prefix) for line in lines):
        out.append(prefix + value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def apply_wizard(
    root: Path,
    *,
    provider: str = "auto",
    api_key: str = "",
    model: str = "",
    embeddings: str = "none",
    context: str = "default",
    memory: str = "sqlite",
    persona: str = "file",
    mcp: tuple[str, ...] = (),
    owner_name: str = "",
    assistant_name: str = "assistant",
    tone: str = "",
    timezone: str = "UTC",
    sleep_hour: str = "4",
    env_path: Path | None = None,
    manifest_path: Path | None = None,
) -> None:
    """Write the profile, the provider, and the chosen parts. Never prints the key."""
    from iris_ai.cli.toml_edit import upsert
    from iris_ai.providers import PROVIDERS, qualify

    apply_setup(
        root,
        owner_name=owner_name,
        assistant_name=assistant_name or "assistant",
        tone=tone,
        timezone=timezone or "UTC",
        sleep_hour=sleep_hour or "4",
        persona="",
    )
    manifest = manifest_path or Path(settings.harness_config)
    spec = PROVIDERS.get(provider.strip().lower()) if provider else None
    if provider and provider != "auto":
        upsert(manifest, "llm_provider", provider.strip().lower())
    if spec is not None and model.strip():
        upsert(manifest, spec.strong_field, qualify(spec.name, model.strip()))
    if spec is not None and spec.key_env and api_key:
        write_env_key(env_path or Path(".env"), spec.key_env, api_key)
    if embeddings == "none":
        upsert(manifest, "embedding_model", "")
    upsert(manifest, "context", context or "default", table="components")
    upsert(manifest, "memory_backend", memory or "sqlite")
    upsert(manifest, "persona", persona or "file", table="components")
    if persona in {"assistant", "coder", "researcher", "tutor"}:
        from iris_ai.prompt import persona_text

        (root / "PERSONA.md").write_text(persona_text(root, persona) + "\n", encoding="utf-8")
    elif persona == "blank":
        (root / "PERSONA.md").write_text("", encoding="utf-8")
    for name in mcp:
        from iris_ai.cli.mcp import _add_preset

        _add_preset(name)


def run_config(*, yes: bool = False, section: str = "") -> int:
    root = Path(settings.workspace_dir)
    if yes:
        apply_setup(root)
        return 0
    from iris_ai.cli.ask import prompter_for
    from iris_ai.setup.flow import apply_plan, collect

    plan = collect(prompter_for(), section=section or "")
    if plan is None:
        return 1
    apply_plan(plan, root=str(root))
    return 0
