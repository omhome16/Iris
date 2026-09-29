"""Packaging and sample-config discipline (P8).

`uv build` + installing the wheel is CI's `package` job, because building a wheel
inside the test suite would make every local run pay for it. These tests check the
invariants that job depends on: one version source, a declared console entry, and
a sample config whose keys all name real settings.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import iris_ai
from iris_ai.config import Settings

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"
ENV_EXAMPLE = ROOT / ".env.example"

# Environment variables Iris reads directly (not through `Settings`), so their
# presence in the sample config is legitimate. Kept explicit on purpose: the test
# is only useful if the allowlist cannot hide an undocumented knob silently.
DIRECT_ENV = {
    "IRIS_DEBUG",  # CLI flag passthrough
    "IRIS_TEST_POSTGRES_DSN",  # the test suite's separate database
    "IRIS_EVAL_POSTGRES_DSN",  # the eval lab's separate database
}

# Settings that exist for code, not for operators: set by a caller at runtime
# rather than typed into `.env`. Kept as an explicit list so "internal" is a
# decision somebody made, the same way tool classes are declared rather than
# implied.
INTERNAL_SETTINGS: set[str] = {
    # Set by Settings at boot when LLM_PROVIDER is unknown or unusable, so a
    # turn can degrade onto a working provider and `iris doctor` can say why.
    # An operator does not type this into .env.
    "PROVIDER_WARNING",
}


def _pyproject() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_there_is_exactly_one_version_and_hatch_reads_that_one():
    project = _pyproject()["project"]
    assert "version" not in project  # no second copy to drift
    assert project["dynamic"] == ["version"]
    assert _pyproject()["tool"]["hatch"]["version"]["path"] == "src/iris_ai/__init__.py"

    source = (ROOT / "src" / "iris_ai" / "__init__.py").read_text(encoding="utf-8")
    declared = re.search(r'__version__\s*=\s*"([^"]+)"', source).group(1)
    assert declared == iris_ai.__version__


def test_the_console_entry_point_is_declared():
    assert _pyproject()["project"]["scripts"]["iris"] == "iris_ai.cli.main:app"


def test_the_default_profile_templates_are_the_tracked_workspace_files():
    """One neutral default, two places it must exist.

    The wheel carries it (so an installed `iris init` can seed a workspace) and the
    repo tracks it (so a clone reads the same contract without installing
    anything). Byte-equality is the cheap way to make drift impossible — a second
    copy that says something different is worse than no copy.
    """
    templates = ROOT / "src" / "iris_ai" / "templates"
    workspace = ROOT / "workspace"
    pairs = {"AGENTS.md": "AGENTS.md", "WORKSPACE-README.md": "README.md"}
    for packaged, tracked in pairs.items():
        assert (templates / packaged).read_text(encoding="utf-8") == (
            workspace / tracked
        ).read_text(encoding="utf-8"), f"{packaged} and workspace/{tracked} have drifted"


def test_the_neutral_default_does_not_claim_a_persona():
    """Phase 7's other half: the *default* profile is neutral. The personal
    assistant lives in `examples/assistant/`, and the harness's own default must
    not assume Telegram, a name, or a life."""
    text = (ROOT / "src" / "iris_ai" / "templates" / "AGENTS.md").read_text(encoding="utf-8")
    lowered = text.lower()
    for persona in ("telegram", "daily assistant", "morning briefing", "her owner"):
        assert persona not in lowered, f"the default contract assumes {persona!r}"
    assert (ROOT / "examples" / "assistant" / "workspace" / "AGENTS.md").is_file(), (
        "the personal-assistant profile must live in examples/assistant/"
    )


def test_the_manual_exists_and_names_the_python_floor():
    """The support matrix lives in the manual now, and the floor has to be in both
    places or one of them is a claim nobody checked."""
    doc = (ROOT / "DOCS.md").read_text(encoding="utf-8")
    assert "requires-python" in doc or "Python" in doc
    assert _pyproject()["project"]["requires-python"] == ">=3.12"


def _env_keys() -> list[str]:
    keys: list[str] = []
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        keys.append(line.split("=", 1)[0].strip())
    return keys


def test_every_sample_config_key_names_a_real_setting():
    """A `.env.example` that documents a knob nobody reads is a lie."""
    fields = {name.upper() for name in Settings.model_fields}
    unknown = sorted({key for key in _env_keys() if key.upper() not in fields and key not in DIRECT_ENV})
    assert not unknown, f"sample-config keys with no setting: {unknown}"


def test_the_sample_config_is_not_empty():
    assert len(_env_keys()) > 20  # it is the documented surface, not a stub


def test_every_setting_is_documented_in_the_sample_config():
    """The reverse of the check above.

    That one stops the sample config from documenting a knob nobody reads; this
    one stops a knob existing that the sample config never mentions. Together
    they make `.env.example` the actual surface rather than a best-effort
    sketch: an operator cannot discover a limit that is silently shaping
    behaviour, and the P8 audit found fifty such settings.
    """
    keys = {key.upper() for key in _env_keys()}
    undocumented = sorted(
        name.upper()
        for name in Settings.model_fields
        if name.upper() not in keys and name.upper() not in INTERNAL_SETTINGS
    )
    assert not undocumented, f"settings missing from .env.example: {undocumented}"


def test_the_sample_config_actually_loads():
    """A sample config that cannot be parsed is worse than a missing one.

    `cp .env.example .env` is the documented first step, and pydantic-settings
    requires **JSON** for a complex-typed setting (list/tuple/dict) because it
    decodes those *before* validation. The sample shipped
    `DREAM_LIGHT_WEIGHTS=0.25,0.3,...`, which is the shape a human would write and
    which raised `SettingsError` from `iris_ai.config` on *every* command — so
    the quickstart was broken from the first line, and nothing caught it because
    the tests never constructed `Settings` from the sample. Now one does.
    """
    from iris_ai.config import Settings

    settings = Settings(_env_file=ENV_EXAMPLE)
    assert settings.dream_light_weights == (0.25, 0.3, 0.1, 0.1, 0.25)


def test_the_wheel_force_includes_the_shipped_skills():
    """Pins the packaging contract, not just today's build.

    `packages = ["src/iris_ai"]` alone leaves the repo-root `skills/` out of the
    wheel, so an installed copy had no builtins at all.
    """
    wheel = _pyproject()["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["force-include"]["skills"] == "iris_ai/builtin_skills"


def test_the_builtin_skill_is_discoverable():
    """The README advertises `web-page-to-notes` as the format's proof; a
    checkout resolves `<repo>/skills/` and an installed wheel resolves the
    packaged copy, but either way it has to be there."""
    from iris_ai.skills.registry import builtin_root

    root = builtin_root()
    assert root is not None, "no builtin root — the shipped skill would vanish"
    assert (root / "web-page-to-notes" / "SKILL.md").is_file()


def test_env_example_documents_the_p8_knobs():
    keys = {key.upper() for key in _env_keys()}
    for expected in (
        "TOOL_GUARD_ENABLED",
        "BUDGET_MAX_TOKENS_PER_DAY",
        "APPROVAL_BIND_DIGEST",
        "COMPUTER_ENABLED",
        "TOOL_SURFACE_BUDGET",
    ):
        assert expected in keys, expected
